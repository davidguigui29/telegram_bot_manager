import threading
import logging
import asyncio
import base64
import os
import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CallbackQueryHandler, ChatMemberHandler
from telegram.error import BadRequest, Forbidden
from telegram.constants import ParseMode
import odoo
from odoo.addons.myfansbook_core.utils.helpers import reclaim_telegram_username, validate_username
from odoo.addons.myfansbook_core.utils.helpers import (
    check_password_strength,
    is_email_taken,
    is_phone_taken,
    validate_email as email_validator
)
from random import choice
import random

_logger = logging.getLogger(__name__)
# LOG_FILE = "message_id.txt"
# ALLOWED_COMMANDS = ["start", "setup_post", "hello"]
# Registration States
# Constants at the top
CHOOSING_METHOD, WAITING_EMAIL, WAITING_PASSWORD, WAITING_OTP, WAITING_PHONE, WAITING_LINK_LOGIN, WAITING_LINK_PASSWORD = range(7)
# CHOOSING_METHOD, WAITING_EMAIL, WAITING_PASSWORD, WAITING_OTP, WAITING_PHONE = range(5)

class TelegramBotThread(threading.Thread):
    def __init__(self, dbname, token, config):
        super().__init__()
        self.daemon = True
        self.dbname = dbname
        self.token = token
        self.config = config
        self.application = None # Store application to access it later
        self.loop = None        # Store loop to stop it safely

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.application = Application.builder().token(self.token).build()

        # 1. The Registration Conversation (MOVE THIS TO THE TOP)
        reg_conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start_command),
            CallbackQueryHandler(self.start_command, pattern="^restart_start$"),

            ],
            states={
                CHOOSING_METHOD: [
                    CallbackQueryHandler(self.email_choice, pattern="^reg_email$"),
                    CallbackQueryHandler(self.phone_choice, pattern="^reg_phone$"),
                    CallbackQueryHandler(self.registration_choice_menu, pattern="^reg_start_choice$"),
                    CallbackQueryHandler(self.link_account_choice, pattern="^link_existing$"),
                    CallbackQueryHandler(self.cancel_reg, pattern="^cancel_reg$"),
                ],
                WAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_email)],
                WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_password)],
                WAITING_PHONE: [MessageHandler(filters.CONTACT, self.process_phone)],
                WAITING_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.finalize_registration)],
                WAITING_LINK_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_link_login)],
                WAITING_LINK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_link_password)],

                WAITING_LINK_LOGIN: [
                # 1. Listen for the Cancel button click (Callback)
                CallbackQueryHandler(self.cancel_reg, pattern="^cancel_reg$"),
                # 2. Listen for the actual text input
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_link_login),
            ],
            
            WAITING_LINK_PASSWORD: [
                # Add it here too so they can cancel while entering the password
                CallbackQueryHandler(self.cancel_reg, pattern="^cancel_reg$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_link_password),
            ],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_reg)],
            per_message=False,
        )
        self.application.add_handler(reg_conv)

        # self.application.add_handler(CallbackQueryHandler(self.start_command, pattern="^restart_start$"))
        

        # Welcome Handler for new members
        self.application.add_handler(ChatMemberHandler(self.welcome_new_member, ChatMemberHandler.CHAT_MEMBER))

        # 2. Handlers that run OUTSIDE the conversation (AFTER ConversationHandler)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.link_handler))
        self.application.add_handler(CommandHandler("hello", self.greetings))
        self.application.add_handler(CommandHandler("setup_post", self.post_welcome_button))

  
        self.application.add_handler(CommandHandler("clear", self.clear_chat))


        # 3. Catch-all (STAYS LAST)
        self.application.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))

        _logger.info(f"Telegram Bot Started for DB: {self.dbname}")
        # Define which updates the bot should listen for
        # If we don't include 'chat_member', the welcome_new_member function never triggers
        allowed_updates = ["message", "callback_query", "chat_member", "my_chat_member"]

        self.application.run_polling(
            close_loop=False, 
            stop_signals=False,
            allowed_updates=allowed_updates # CRITICAL ADDITION
        )
    


    def stop_polling(self):
        """ Method called from the Odoo Main Thread to stop the bot """
        if self.application and self.application.running:
            _logger.info("Stopping Telegram Bot for DB: %s", self.dbname)
            
            # Use the loop to schedule the shutdown tasks
            try:
                # We use a threadsafe call to stop the polling first
                # This breaks the run_polling() block
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
                future.result(timeout=10) 
            except Exception as e:
                _logger.error("Error during bot shutdown: %s", e)
            finally:
                # Force the loop to stop if it hasn't already
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.loop.stop)

    async def _shutdown(self):
        """ Private coroutine to handle async shutdown sequences """
        try:
            # 1. Stop the updater/polling first
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            
            # 2. Stop the application logic
            if self.application.running:
                await self.application.stop()
            
            # 3. Final shutdown of network transports
            await self.application.shutdown()
        except Exception as e:
            _logger.warning("Graceful shutdown encountered an issue: %s", e)

    def trigger_odoo_otp(self, email, name, otp_code):
        try:
            db_registry = odoo.modules.registry.Registry(self.dbname)
            with db_registry.cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                
                # 1. Save to your existing otp.verification model
                env['otp.verification'].sudo().create({
                    'otp': otp_code,
                    'email': email,
                    'state': 'unverified'
                })

                # 2. Build the Email using your existing template helper
                # Importing exactly like your otp_signup.py does
                from odoo.addons.otp_login.utils.email_templates import otp_signup_html
                
                company = env.company
                base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
                
                body_html = otp_signup_html(
                    company_logo=f"{base_url}/web/image/res.company/{company.id}/logo" if company.logo else "",
                    company_name="Myfansbook",
                    name=name,
                    otp_code=otp_code,
                    company_phone=company.phone or "N/A",
                    company_website=company.website or base_url
                )

                # 3. Send the mail
                mail_values = {
                    'subject': f"[{'Myfansbook'}] Your Verification Code",
                    'body_html': body_html,
                    'email_to': email,
                    'email_from': company.email or "noreply@myfansbook.com",
                }
                env['mail.mail'].sudo().create(mail_values).send()
                
                cr.commit()
            return True
        except Exception as e:
            _logger.error(f"OTP Email Error: {e}")
            return False

    def get_odoo_user(self, tg_user):
        """Helper to query Odoo using permanent ID first, then username."""
        tg_id = str(tg_user.id)
        tg_handle = tg_user.username # This will be None/False if not set
        
        db_registry = odoo.modules.registry.Registry(self.dbname)
        with db_registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            
            # Construct the domain dynamically
            # We ALWAYS search by ID
            domain = [('telegram_id', '=', tg_id)]
            
            # ONLY add the username to the search if it exists
            # This prevents matching a user with no username against an Odoo record with no username
            if tg_handle:
                domain = ['|'] + domain + [('telegram_username', '=', tg_handle)]
            
            user_profile = env['myfans.user'].search(domain, limit=1)
            
            if user_profile:
                vals = {}
                # Update username if it changed or was newly set
                if tg_handle and user_profile.telegram_username != tg_handle:
                    vals['telegram_username'] = tg_handle
                
                # Auto-link the ID if we found them via username but ID was missing
                if not user_profile.telegram_id:
                    vals['telegram_id'] = tg_id
                
                if vals:
                    user_profile.sudo().write(vals)
                    cr.commit()

                return {
                    'allowed': user_profile.allowed_url_message,
                    'name': user_profile.display_name,
                    'status': user_profile.account_status,
                    'phone': user_profile.phone,
                    'email': user_profile.email,
                }
        return None

    async def cancel_reg(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancels and ends the conversation, handling both command and button input."""
        query = update.callback_query

        # Define the "Start" button keyboard
        start_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Restart Registration 🔄", callback_data="restart_start")]
        ])
        
        if query:
            # If triggered by a button, acknowledge the click and edit the existing message
            await query.answer()
            await query.edit_message_text(
                "Registration cancelled. You can use the button bellow or can type /start whenever you're ready to try again.",
                reply_markup=start_keyboard
            )
        else:
            # If triggered by /cancel command
            await update.message.reply_text(
                "Registration cancelled. You can type /start whenever you're ready to try again.",
                reply_markup=start_keyboard
                # reply_markup=ReplyKeyboardRemove()
            )

        context.user_data.clear()
        return ConversationHandler.END

    async def clear_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Deletes recent messages in the group. Admin only."""
        chat = update.effective_chat
        user = update.effective_user

        # 1. Only work in groups
        if chat.type == "private":
            await update.message.reply_text("This command only works in groups.")
            return

        # 2. Security Check: Is the user an admin?
        # We reuse your existing is_user_admin logic but check for the current chat
        try:
            member = await chat.get_member(user.id)
            print(f"DEBUG: User {user.id} status in {chat.id} is: {member.status} and the username is: {user.username}")
            # username is: GroupAnonymousBot
            if member.status not in ["left",'administrator', 'creator'] and user.id != self.config['OWNER_ID']:
                await update.message.reply_text("❌ Unauthorized: Only admins can clear the chat.")
                return
        except Exception as e:
            _logger.error(f"Error checking admin status for clear: {e}")
            return

        # 3. Message Deletion Loop
        message_id = update.message.message_id
        deleted_count = 0
        
        # Notify user that clearing has started
        status_msg = await update.message.reply_text("🧹 Clearing chat...")

        # Telegram doesn't allow "Fetch all", so we try to delete by ID range backwards
        # We attempt to delete the last 100 IDs
        for i in range(message_id, message_id - 100, -1):
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=i)
                deleted_count += 1
            except BadRequest as e:
                # This happens if message is > 48h old or already deleted
                continue
            except Exception as e:
                _logger.error(f"Clear chat error at ID {i}: {e}")

        # Send confirmation and set it to auto-delete after 5 seconds
        final_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ Cleaned {deleted_count} messages."
        )
        
        # Optional: Delete the "Cleaned" notification after 5 seconds
        context.job_queue.run_once(self.delete_notification, 5, data={'chat_id': chat.id, 'message_id': final_msg.message_id})

    async def delete_notification(self, context: ContextTypes.DEFAULT_TYPE):
        """Helper to delete the 'Cleaned' status message."""
        job = context.job
        await context.bot.delete_message(chat_id=job.data['chat_id'], message_id=job.data['message_id'])


    async def start_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Initial choice between Email and Phone."""
        keyboard = [
            [InlineKeyboardButton("Sign up with Email 📧", callback_data="reg_email")],
            [InlineKeyboardButton("Sign up with Phone 📱", callback_data="reg_phone")],
            [InlineKeyboardButton("Cancel Registration ❌", callback_data="cancel_reg")] 
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Welcome! To join the group, you need an account.\nHow would you like to register?",
            reply_markup=reply_markup
        )
        return CHOOSING_METHOD

    async def email_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="Please enter your <b>Email Address</b>:", parse_mode=ParseMode.HTML)
        return WAITING_EMAIL

    

    async def phone_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Use KeyboardButton for actual phone sharing
        btn = [[KeyboardButton("Share My Telegram Number 📲", request_contact=True)]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Click the button below to share your phone number safely:",
            reply_markup=ReplyKeyboardMarkup(btn, one_time_keyboard=False, resize_keyboard=True)
        )
        return WAITING_PHONE

    async def process_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        contact = update.message.contact
        phone = contact.phone_number

        # Ensure phone starts with + for consistency in Odoo
        if not phone.startswith('+'):
            phone = f"+{phone}"

        db_registry = odoo.modules.registry.Registry(self.dbname)
        with db_registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})

                
            if is_phone_taken(env, phone):
                await update.message.reply_text(
                    "⚠️ This phone number is already linked to an account.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return ConversationHandler.END

        context.user_data['reg_login'] = phone
        context.user_data['reg_phone'] = phone
        context.user_data['reg_type'] = 'phone'
        
        await update.message.reply_text(
            "✅ Phone verified! Now, please set a password for your account.\n"
            "Must be at least 8 characters long.",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_PASSWORD

    async def process_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        email = update.message.text.strip().lower()
        
        # Using your imported helper: email_validator
        # Based on your helpers.py, this returns True or False
        if not email_validator(email):
            await update.message.reply_text(
                "❌ <b>Invalid Email Format</b>\n"
                "The email address you entered is not valid. Please try again:",
                parse_mode=ParseMode.HTML
            )
            return WAITING_EMAIL # Stay in this state to wait for a correct email
        
        # 2. Database Check (Create a thread-safe env)
        db_registry = odoo.modules.registry.Registry(self.dbname)
        with db_registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                
            if is_email_taken(env, email):
                await update.message.reply_text("⚠️ This email is already registered. Please use another:")
                return WAITING_EMAIL
        
        # If valid, store data and move to password
        context.user_data['reg_login'] = email
        context.user_data['reg_type'] = 'email'
        
        await update.message.reply_text(
            f"✅ Email <code>{email}</code> accepted!\n\n"
            "Now, please set a <b>Strong Password</b>.\n"
            "<i>(Min 8 chars, must include Uppercase, Lowercase, and a Number)</i>",
            parse_mode=ParseMode.HTML
        )
        return WAITING_PASSWORD


    async def process_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        password = update.message.text
        strength_result = check_password_strength(password)
        
        if strength_result and 'error' in strength_result:
            await update.message.reply_text(
                f"⚠️ <b>Password too weak!</b>\nReason: {strength_result['error']}\n\nPlease try again:",
                parse_mode=ParseMode.HTML
            )
            return WAITING_PASSWORD

        context.user_data['reg_password'] = password
        
        if context.user_data.get('reg_type') == 'email':
            email = context.user_data.get('reg_login')
            name = update.effective_user.first_name or "User"
            
            # Generate 4-digit OTP (matching your otp_signup.py logic)
            otp_code = "".join([str(random.randint(0, 9)) for _ in range(4)])
            context.user_data['otp_code'] = otp_code # Store for validation
            
            # Trigger Odoo to save OTP and send Email
            success = self.trigger_odoo_otp(email, name, otp_code)
            
            if success:
                await update.message.reply_text(
                    f"📧 <b>Verification Required</b>\n"
                    f"A 4-digit code has been sent to <b>{email}</b>. Please enter it here:",
                    parse_mode=ParseMode.HTML
                )
                return WAITING_OTP
            else:
                await update.message.reply_text("❌ Failed to send email. Please try again later.")
                return ConversationHandler.END
        else:
            return await self.finalize_registration(update, context)



    async def process_otp(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        entered_code = update.message.text.strip()
        actual_code = context.user_data.get('otp_code')

        if entered_code == str(actual_code):
            # Mark email as verified in user_data if needed
            return await self.finalize_registration(update, context)
        else:
            await update.message.reply_text(
                "❌ <b>Incorrect Code</b>\n"
                "Please check your email and enter the correct 4-digit code:",
                parse_mode=ParseMode.HTML
            )
            return WAITING_OTP



    async def finalize_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data
        login = data.get('reg_login')
        password = data.get('reg_password')
        phone = data.get('reg_phone')

        # 1. Collect Full Name and Username
        first_name = update.effective_user.first_name or ""
        last_name = update.effective_user.last_name or ""
        full_name = f"{first_name} {last_name}".strip()
        name = full_name or update.effective_user.username or "Telegram User"


        tg_username = update.effective_user.username
        user_id = update.effective_user.id
        tg_bio = ""
        tg_dob = False

        try:
            # We must get the full Chat object to see the 'bio' field
            full_chat = await context.bot.get_chat(user_id)
            print(f"DEBUG: Bio is: {full_chat}")
            tg_bio = full_chat.bio or ""

            # 2. Collect Birthday (if available and shared by user)
            if full_chat.birthdate:
                bd = full_chat.birthdate
                # format for Odoo (YYYY-MM-DD). If year is hidden, we use a placeholder or handle it.
                year = bd.year or 1900 
                tg_dob = f"{year}-{bd.month:02d}-{bd.day:02d}"
                print(f"DEBUG: Birthday is: {tg_dob}")
        except Exception as bio_err:
            _logger.warning(f"Could not fetch user bio: {bio_err}")

    
        # 3. Get Profile Picture
        profile_image_base64 = False
        try:
            user_id = update.effective_user.id
            # Get list of profile photos (returns a list of PhotoSize objects)
            photos = await context.bot.get_user_profile_photos(user_id, limit=1)
            
            if photos.total_count > 0:
                # Get the largest size of the most recent photo
                file_id = photos.photos[0][-1].file_id
                new_file = await context.bot.get_file(file_id)
                
                # Download file into memory
                image_bytes = await new_file.download_as_bytearray()
                profile_image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        except Exception as photo_err:
            _logger.warning(f"Could not fetch profile photo: {photo_err}")

        try:
            db_registry = odoo.modules.registry.Registry(self.dbname)
            with db_registry.cursor() as cr:
                # Create environment with tg_username in context
                # This triggers the automatic profile creation logic in your res_users.py
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {
                    'tg_username': tg_username,
                    'tg_bio': tg_bio,
                    'tg_id': user_id,
                     
                })
                
                # 1. Find Company
                company = env['res.company'].sudo().search([('name', 'ilike', 'Myfansbook')], limit=1)
                if not company:
                    company = env['res.company'].sudo().search([], limit=1)

                # 2. Create User 
                # Your res_users.py 'create' override will handle 
                # reclaim_telegram_username and myfans.user creation automatically!
                user_vals = {
                    'name': name,
                    'login': login,
                    'password': password,
                    'company_id': company.id,
                    'image_1920': profile_image_base64,
                    'company_ids': [(6, 0, [company.id])],
                    'groups_id': [(4, env.ref('base.group_portal').id)],
                }
                if phone:
                    user_vals['phone'] = phone

                if data.get('reg_type') == 'email':
                    user_vals['email'] = login

                env['res.users'].sudo().create(user_vals)
                
                cr.commit()


            # Send Private Message with Web App link
            private_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Login to your account🔗", url=self.config['TELEGRAM_WEB_APP_URL'])],
                [InlineKeyboardButton("Browser Login🔗", url=self.config['DASHBOARD_URL'])],
                [InlineKeyboardButton("Back to Channel", url=self.config['CHANNEL_LINK'])],
                [InlineKeyboardButton("Join Group", url=self.config['GROUP_LINK'])]
            ])

            await update.message.reply_text(
                f"🎉 <b>Registration Successful!</b>\n\n"
                f"Welcome {name}!\nYour login: <code>{login}</code>",
                reply_markup=private_markup,
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            _logger.error(f"Registration Error: {e}")
            await update.message.reply_text("❌ Registration failed. The username might be taken or a system error occurred.")

        context.user_data.clear()
        return ConversationHandler.END


    async def contact_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        contact = update.effective_message.contact
        user_phone = contact.phone_number
        user_id = update.effective_user.id
        
        # 1. Save to Odoo
        # Assuming you have a method to update the user record
        success = self.update_odoo_phone(user_id, user_phone)

        if success:
            await update.message.reply_text(
                f"✅ Thank you! Your phone number ({user_phone}) has been saved.",
                reply_markup=ReplyKeyboardRemove() # This removes the big button
            )
            # Manually trigger start_command to continue the flow
            await self.start_command(update, context)
        else:
            await update.message.reply_text("❌ There was an error saving your number. Please try again.")



    async def link_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat

        # 1. Safety Checks
        if not message or not user or chat.type == "private":
            return

        # Don't process system accounts (ID 777000) or the bot itself
        if user.id == 777000 or user.id == context.bot.id:
            return

        # 2. Owner & Admin Bypass
        if user.id == self.config['OWNER_ID'] or user.id == 1087968824:
            return # Never delete owner messages      

        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            print(f"DEBUG: Owner ID is: {self.config['OWNER_ID']} User {user.id} status in {chat.id} is: {member.status} and the username is: {user.username}")
            if member.status in ['administrator', 'creator', 'left']:
                return
        except Exception:
            pass # If we can't check, proceed to filter  

        if not message.entities:
            return

        has_link = any(e.type in ["url", "text_link"] for e in message.entities)
        if not has_link:
            return

        # Bypass for Owner (as per your script)
        if user.id == self.config['OWNER_ID']:
            return

        # Check Odoo Permissions
        identifier = user.username if user.username else str(user.id)
        odoo_data = self.get_odoo_user(user)

        # Logic: If user not found in Odoo or not allowed_url_message
        if not odoo_data or not odoo_data.get('allowed'):
            try:
                await message.delete()
                
                # Tag user in group
                bot_url = f"https://t.me/{context.bot.username}"
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("Send it Here 🚀", url=bot_url)]])
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Hey {user.mention_html()}, you are not allowed to send links into the group! 🚫",
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )

                # Send Private Message with Web App link
                private_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Login to your account🔗", url=self.config['TELEGRAM_WEB_APP_URL'])],
                    [InlineKeyboardButton("Back to Channel", url=self.config['CHANNEL_LINK'])],
                    [InlineKeyboardButton("Back to Group", url=self.config['GROUP_LINK'])]
                ])
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"Hi {user.first_name}, In order to post links, log into your account and post it there.",
                    reply_markup=private_markup
                )
            except Exception as e:
                _logger.error("Bot Handler Error: %s", e)



    async def is_member(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        try:
            # Note: The bot MUST be an administrator in the channel for this to work reliably
            member = await context.bot.get_chat_member(chat_id=self.config['CHANNEL_ID'], user_id=user_id)
            _logger.info(f"DEBUG: User {user_id} status in {self.config['CHANNEL_ID']} is: {member.status}")
            
            # Check against valid 'joined' statuses
            # Missing colon fixed here:
            if member.status in ['member', 'administrator', 'creator']:
                return True
            return False
        except Exception as e:
            _logger.error(f"DEBUG: Failed to check membership for {user_id}: {e}")
            return False



    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 1. Handle Button Clicks (Callback Queries)
        if update.callback_query:
            await update.callback_query.answer()

        user = update.effective_user
        user_mention = user.mention_html()
        identifier = user.username if user.username else str(user.id)
        
        # Always use self.get_odoo_user logic first
        odoo_data = self.get_odoo_user(user)
        
        # USE update.effective_message INSTEAD OF update.message
        if update.effective_chat.type == "supergroup":
            keyboard = [[InlineKeyboardButton("Go to your Assistant📢", url=f"{self.config['BOT_INBOX_URL']}?start=join")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Changed to update.effective_message
            await update.effective_message.reply_text(
                text=f"Hey {user.mention_html()}! \n\n",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return

        if odoo_data and update.effective_chat.type == "private":
            is_in_channel = await self.is_member(user.id, context)
            
            if not is_in_channel:
                channel_username = self.config['CHANNEL_ID'].replace('@', '')
                keyboard = [[InlineKeyboardButton(f"Join Channel 📢", url=f"https://t.me/{channel_username}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Changed to update.effective_message
                await update.effective_message.reply_text(
                    text=f"Welcome back {odoo_data['name']}! \n\n"
                         f"⚠️ You are registered on our site, but you must join our channel "
                         f"to access the group features.",
                    reply_markup=reply_markup
                )
            else:
                if update.effective_chat.type == "private":
                    keyboard = [
                        [InlineKeyboardButton("Go to Channel", url=self.config['CHANNEL_LINK'])],
                        [InlineKeyboardButton("Go to Group", url=self.config['GROUP_LINK'])],
                        [InlineKeyboardButton("Login to your account🔗", url=self.config['TELEGRAM_WEB_APP_URL'])],
                        [InlineKeyboardButton("Browser Login🔗", url=self.config['DASHBOARD_URL'])],
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    # Changed to update.effective_message
                    await update.effective_message.reply_text(
                        text=f"Welcome back {odoo_data['name']}! You are fully verified. ✅",
                        reply_markup=reply_markup
                    )
                else:
                    await update.effective_message.reply_text(f"Welcome back {odoo_data['name']}! You are fully verified. ✅")
    
            return ConversationHandler.END

        
        else:
            if update.effective_chat.type == "private":            
                keyboard = [
                    [
                        InlineKeyboardButton("Create New Account ✨", callback_data="reg_start_choice"),
                        InlineKeyboardButton("Link My Account 🔗", callback_data="link_existing")
                    ],
                    [InlineKeyboardButton("Cancel ❌", callback_data="cancel_reg")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.effective_message.reply_text(
                    text=(
                        f"<b>Welcome to {self.config['WEBSITE_NAME']}, {user_mention}!</b> 👋\n\n"
                        f"To get started, would you like to create a new profile or "
                        f"link your existing Myfansbook account?"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
                return CHOOSING_METHOD



    async def registration_choice_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Intermediate menu to choose registration method."""
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("Sign up with Email 📧", callback_data="reg_email")],
            [InlineKeyboardButton("Sign up with Phone 📱", callback_data="reg_phone")],
            [InlineKeyboardButton("⬅️ Back", callback_data="cancel_reg")]
        ]
        await query.edit_message_text(
            "How would you like to register?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING_METHOD

    async def link_account_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Prompt user for their website login."""
        keyboard = [
            [InlineKeyboardButton("Cancel ❌", callback_data="cancel_reg")]

        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            text="Please enter your Myfansbook Login (Email/Phone/Username): \nEg. +233xxxxxxxx0",
            reply_markup=reply_markup)

        return WAITING_LINK_LOGIN

    async def process_link_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Store the login and ask for password."""
        keyboard = [
            [InlineKeyboardButton("Cancel", callback_data="cancel_reg")]

        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        context.user_data['link_login'] = update.message.text.strip()
        await update.message.reply_text(
            text="Please enter your password:",
            reply_markup=reply_markup)
        return WAITING_LINK_PASSWORD


    async def process_link_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify credentials and link the account."""
        login = context.user_data.get('link_login')
        password = update.message.text
        # Use effective_user to get the person who sent the message
        tg_user = update.effective_user
        tg_username = tg_user.username if tg_user.username else str(tg_user.id)

        db_registry = odoo.modules.registry.Registry(self.dbname)
        with db_registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            try:
                # Odoo authenticate signature: authenticate(db, credentials, user_agent_env)
                
                user_agent_env = {'interactive': False}
                credentials = {
                    'login': login, 
                    'password': password, 
                    'type': 'password'
                }
                
                # IMPORTANT: Call it via the class or env to match the signature correctly
                result = env['res.users'].authenticate(
                    self.dbname, 
                    credentials,
                    user_agent_env=user_agent_env
                )

                uid = result.get('uid') if isinstance(result, dict) else result
                
                _logger.info(f"DEBUG: Extracted UID for browse: {uid}")


                if uid:
                    user = env['res.users'].sudo().browse(uid)
                    
                    # Link the telegram username to the Partner (res.partner)
                    user.partner_id.write({
                        'telegram_id': str(tg_user.id),
                        'telegram_username': tg_username,
                        
                        })
                    
                    # Also link to the MyFans profile (myfans.user) if it exists
                    # profile = env['myfans.user'].sudo().search([('user_id', '=', uid)], limit=1)
                    # if profile:
                    #     profile.write({'telegram_username': tg_username})

                    cr.commit()
                    
                    await update.message.reply_text(
                        f"✅ Success! Your Telegram account is now linked to <b>{user.name}</b>.\n\n"
                        "You are now fully verified.",
                        parse_mode=ParseMode.HTML
                    )
                    return ConversationHandler.END
                
            except odoo.exceptions.AccessDenied:
                # This is the standard Odoo error for wrong credentials
                await update.message.reply_text("❌ Invalid login or password. Authentication failed.")
                return await self.show_retry_menu(update)
                
            except Exception as e:
                _logger.error(f"Linking Error: {str(e)}")
                await update.message.reply_text("❌ A technical error occurred. Please try again later.")
                return ConversationHandler.END

    async def show_retry_menu(self, update):
        keyboard = [
            [InlineKeyboardButton("Try Login Again 🔑", callback_data="link_existing")],
            [InlineKeyboardButton("Sign up instead ✨", callback_data="reg_start_choice")]
        ]
        await update.message.reply_text(
            "Would you like to try again or create a new account?", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING_METHOD

    # async def process_link_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    #     """Verify credentials and link the account."""
    #     login = context.user_data.get('link_login')
    #     password = update.message.text
    #     tg_username = update.effective_user.username

    #     db_registry = odoo.modules.registry.Registry(self.dbname)
    #     with db_registry.cursor() as cr:
    #         env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
    #         try:
    #             # 1. Authenticate with Odoo
    #             uid = env['res.users'].authenticate(self.dbname, login, password)
    #             if uid:
    #                 # 2. Link the telegram_username to this user
    #                 user = env['res.users'].browse(uid)
    #                 # We update both the partner (for storage) and the context logic
    #                 user.partner_id.sudo().write({'telegram_username': tg_username})
                    
    #                 # If you have the myfans.user model, ensure it's synced
    #                 profile = env['myfans.user'].sudo().search([('user_id', '=', uid)], limit=1)
    #                 if profile:
    #                     profile.write({'telegram_username': tg_username})

    #                 cr.commit()
                    
    #                 await update.message.reply_text(
    #                     f"✅ Success! Your Telegram account is now linked to <b>{user.name}</b>.\n"
    #                     "You can now access all features.",
    #                     parse_mode=ParseMode.HTML
    #                 )
    #                 return ConversationHandler.END
                
    #         except Exception as e:
    #             _logger.error(f"Linking Error: {e}")
    #             await update.message.reply_text("❌ Invalid login or password. Would you like to try again or sign up?")
    #             # Provide buttons to try again or switch to signup
    #             keyboard = [
    #                 [InlineKeyboardButton("Try Login Again 🔑", callback_data="link_existing")],
    #                 [InlineKeyboardButton("Sign up instead ✨", callback_data="reg_email")]
    #             ]
    #             await update.message.reply_text("Choose an option:", reply_markup=InlineKeyboardMarkup(keyboard))
    #             return CHOOSING_METHOD

    
    async def welcome_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Greets new members and validates their status immediately."""
        _logger.info(f"DEBUG: Chat Member Update Received: {update.chat_member}")
        result = update.chat_member
        
        # Check if the status changed to 'member' (meaning they just joined)
        if result.new_chat_member.status == "member":
            user = result.new_chat_member.user
            chat = update.effective_chat
            
            # Safety check: only run in groups
            if chat.type not in ["group", "supergroup"]:
                return

            identifier = user.username if user.username else str(user.id)
            odoo_data = self.get_odoo_user(user)
            
            # --- CASE 1: NOT ON WEBSITE ---
            if not odoo_data:
                welcome_text = (
                    f"Welcome {user.mention_html()}! 👋\n\n"
                    f"We couldn't find an account linked to your Telegram. "
                    f"Please register via our private chat."
                )
                keyboard = [[InlineKeyboardButton("Click to Register 📝", url=f"https://t.me/{context.bot.username}?start=join")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

            # --- CASE 2: ON WEBSITE, BUT NOT IN CHANNEL ---
            else:
                is_in_channel = await self.is_member(user.id, context)
                if not is_in_channel:
                    channel_username = self.config['CHANNEL_ID'].replace('@', '')
                    welcome_text = (
                        f"Welcome back {odoo_data['name']}! 👋\n\n"
                        f"You are registered on our site, but you must join our official "
                        f"channel to participate in the group."
                    )
                    keyboard = [[InlineKeyboardButton("Join Channel 📢", url=f"https://t.me/{channel_username}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                
                # --- CASE 3: FULLY VERIFIED ---
                else:
                    welcome_text = (
                        f"Welcome {user.mention_html()}! 🎉\n\n"
                        f"You are fully verified and registered. Enjoy the community!"
                    )
                    reply_markup = None # No buttons needed for verified users

            # Send the final message to the group
            await context.bot.send_message(
                chat_id=chat.id,
                text=welcome_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )

    async def is_user_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Checks if the user sending the command is an admin in the target channel."""
        user_id = update.effective_user.id
        try:
            member = await context.bot.get_chat_member(chat_id=self.config['CHANNEL_ID'], user_id=user_id)
            return member.status in ['administrator', 'creator']
        except Exception as e:
            logging.error(f"Error checking admin status: {e}")
            return False

    async def post_welcome_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Restricted to Admins: Deletes old post, sends new one, and pins it."""
        # Access config values via self.config
        LOG_FILE = self.config.get('LOG_FILE', 'message_id.txt')
        WEBSITE_NAME = self.config.get('WEBSITE_NAME', 'Myfansbook')
        CHANNEL_ID = self.config.get('CHANNEL_ID')

        if not await self.is_user_admin(update, context):
            await update.message.reply_text("❌ Unauthorized: This command is restricted to channel admins.")
            return

        bot_url = f"https://t.me/{context.bot.username}?start=join"
        keyboard = [[InlineKeyboardButton(f"Get Access to {WEBSITE_NAME} Group 🚀", url=bot_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            # Handle old message deletion using the configured LOG_FILE
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f:
                    old_id = f.read().strip()
                    if old_id:
                        try:
                            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(old_id))
                        except Exception: 
                            pass

            # Send and Pin the new message
            new_msg = await context.bot.send_message(
                chat_id=CHANNEL_ID, 
                text=f"Welcome to {WEBSITE_NAME}!", 
                reply_markup=reply_markup
            )
            
            await context.bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=new_msg.message_id)

            # Log the new ID
            with open(LOG_FILE, "w") as f:
                f.write(str(new_msg.message_id))
                
            await update.message.reply_text(f"✅ Success! Post updated and pinned in {CHANNEL_ID}.")

        except Exception as e:
            _logger.error("Error in post_welcome_button: %s", e)



    # Greetings function
    async def greetings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # User(first_name='David', id=8484782939, is_bot=False, language_code='en', last_name='GUIGUI', username='guidassignal')
        # User(first_name='Group', id=1087968824, is_bot=True, username='GroupAnonymousBot')


        print(f"DEBUG: User is: {update.effective_user}")
        print(f"DEBUG: Chat is: {update.effective_chat}")
        print(f"DEBUG: Username is: {update.effective_user.username}")


        # Check if it is group anonymous bot
        # i
        if update.effective_user.username == "GroupAnonymousBot":
            await update.message.reply_text(f"Hello, {self.config['WEBSITE_NAME']} Family!")
        
        elif update.effective_chat.type == "private":
            await update.message.reply_text(f"Hello {update.effective_user.first_name}! How can I help you?")

        elif update.effective_chat.type == "supergroup" and update.effective_user.username != "GroupAnonymousBot":
            await update.message.reply_text(f"Hey {update.effective_user.first_name}!")




    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Sends a warning if a user enters a command not in the allowed list."""
        if update.message and update.message.text.startswith('/'):
            command = update.message.text.split()[0].replace('/', '')
            if command not in self.config['ALLOWED_COMMANDS']:
                await update.message.reply_text(
                    f"🚫 Warning: '{command}' is not a recognized or allowed command."
                )
