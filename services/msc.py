import threading
import logging
import asyncio
import os
import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
from telegram.error import BadRequest, Forbidden
from telegram.constants import ParseMode
import odoo
from odoo.addons.myfansbook_core.utils.helpers import reclaim_telegram_username, validate_username, validate_email as email_validator


_logger = logging.getLogger(__name__)

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
        # 1. Initialize the loop for the background thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # 2. Build the application
        self.application = Application.builder().token(self.token).build()

        # 3. Add Handlers
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.link_handler))
        # self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("hello", self.greetings))
        self.application.add_handler(CommandHandler("setup_post", self.post_welcome_button))

        self.application.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))
        self.application.add_handler(MessageHandler(filters.CONTACT, self.contact_handler))

        reg_conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start_command)], # If not in Odoo, start_command should return CHOOSING_METHOD
            states={
                CHOOSING_METHOD: [
                    CallbackQueryHandler(self.email_choice, pattern="^reg_email$"),
                    CallbackQueryHandler(self.phone_choice, pattern="^reg_phone$"),
                ],
                WAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_email)],
                WAITING_PHONE: [MessageHandler(filters.CONTACT, self.process_phone)],
                WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_password)],
                WAITING_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.finalize_registration)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_reg)],
        )
        self.application.add_handler(reg_conv)



        _logger.info("Telegram Bot Starting for DB: %s", self.dbname)
        
        # 4. IMPORTANT FIX: Set stop_signals=False
        # This prevents the library from trying to use signal.set_wakeup_fd
        # which is only allowed in the main thread.
        self.application.run_polling(
            close_loop=False, 
            stop_signals=False  # Required for background threads
        )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_mention = user.mention_html()
        identifier = user.username if user.username else str(user.id)
        identifier_styled = f"<code>{identifier}</code>"

        
        # Always use self.get_odoo_user logic first
        odoo_data = self.get_odoo_user(identifier)
        print(f"DEBUG: Odoo Data is: {odoo_data}")
        # await update.message.reply_text("🔄 Checking your membership status, please wait...")

        if odoo_data:

            

            # Check if they are in the Telegram Channel
            is_in_channel = await self.is_member(user.id, context)
            
            if not is_in_channel:
                # User is in Odoo but NOT in the Telegram Channel
                channel_username = self.config['CHANNEL_ID'].replace('@', '')
                keyboard = [[InlineKeyboardButton(f"Join Channel 📢", url=f"https://t.me/{channel_username}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    text=f"Welcome back {odoo_data['name']}! \n\n"
                         f"⚠️ You are registered on our site, but you must join our channel "
                         f"to access the group features.",
                    reply_markup=reply_markup
                )
            else:
                # Check if it is a private chat with the bot
                print(update.effective_chat.type)
                if update.effective_chat.type == "private":
                    keyboard = [
                        [InlineKeyboardButton("Back to Channel", url=self.config['CHANNEL_LINK'])],
                        [InlineKeyboardButton("Back to Group", url=self.config['GROUP_LINK'])]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await update.message.reply_text(
                        text=f"Welcome back {odoo_data['name']}! You are fully verified. ✅",
                        reply_markup=reply_markup
                    )

                else:
                    # User is in Odoo AND in the Channel
                    await update.message.reply_text(f"Welcome back {odoo_data['name']}! You are fully verified. ✅")
        
        else:
            # NEW LOGIC: Check if phone is missing in your Odoo database
            
            # Create a Reply Keyboard (not Inline) to request contact
            contact_keyboard = [
                [KeyboardButton("Share Phone Number 📱", request_contact=True)]
            ]
            reply_markup = ReplyKeyboardMarkup(contact_keyboard, one_time_keyboard=True, resize_keyboard=True)

            await update.message.reply_text(
                text=f"Welcome {user_mention}! To complete your verification, please share your phone number using the button below.",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML

            )
            return # Stop here until they share the contact


            # Add an inline keyboard to send them to the telegram mini web app
            # Extract the username without the @ for the link
            channel_username = self.config['CHANNEL_ID'].replace('@', '')
            bot_username = context.bot.username

            keyboard = [
                # Main Task Button
                [InlineKeyboardButton("Get linked 🔗", url=self.config['TELEGRAM_WEB_APP_URL'])],
                
                # Refresh/Restart Button (Points back to the bot)
                [InlineKeyboardButton("🔄 Re-verify / Start", url=f"https://t.me/{bot_username}?start=check")],
                
                # Back to Channel
                [InlineKeyboardButton("Go back to Channel 📢", url=f"https://t.me/{channel_username}")]
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # keyboard = [[InlineKeyboardButton("Finish Task 🔗", url=f"{self.config['TELEGRAM_WEB_APP_URL']}")], [InlineKeyboardButton("Go back to Channel 🔗", url=f"https://t.me/{self.config['CHANNEL_ID'].replace('@', '')}")], ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                text=(
                    f"<b>Welcome, {user_mention}!</b> ✨\n\n"
                    f"It looks like your account isn't linked to our website yet. To get started, "
                    f"please register on our website and add this username to your profile. Tap on it below to copy it.\n After copying the username, Click on the Get linked button below:\n\n"
                    f"👉 {identifier_styled}\n\n"
                    f"<i>Once updated, click start again!</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            # [InlineKeyboardButton("Go to Group 🔗", url=f"{GROUP_LINK}")]