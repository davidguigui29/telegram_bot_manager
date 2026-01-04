import threading
import logging
import asyncio
import os
import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import BadRequest, Forbidden
from telegram.constants import ParseMode
import odoo

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
        self.application.add_handler(CommandHandler("start", self.start_command))

        _logger.info("Telegram Bot Starting for DB: %s", self.dbname)
        
        # 4. IMPORTANT FIX: Set stop_signals=False
        # This prevents the library from trying to use signal.set_wakeup_fd
        # which is only allowed in the main thread.
        self.application.run_polling(
            close_loop=False, 
            stop_signals=False  # Required for background threads
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
    # def stop_polling(self):
    #     """ Method called from the Odoo Main Thread to stop the bot """
    #     # Updated check: ensure application exists and is initialized
    #     if self.application:
    #         _logger.info("Stopping Telegram Bot for DB: %s", self.dbname)
            
    #         # Use the loop to schedule the shutdown tasks
    #         # Since we used stop_signals=False, we must manually trigger stop
    #         try:
    #             future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
    #             future.result(timeout=10) 
    #         except Exception as e:
    #             _logger.error("Error during bot shutdown: %s", e)

    # async def _shutdown(self):
    #     """ Private coroutine to handle async shutdown sequences """
    #     # Check if application is actually running before stopping
    #     if self.application.running:
    #         await self.application.stop()
        
    #     await self.application.shutdown()
        
    #     if self.loop.is_running():
    #         self.loop.stop()


    def get_odoo_user(self, telegram_handle):
        """Helper to query Odoo database for the user profile"""
        # Create a new registry environment for this thread
        db_registry = odoo.modules.registry.Registry(self.dbname)
        with db_registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            # Search for the user in your MyFansUser model
            # We search by telegram_username (case insensitive) or ID
            user_profile = env['myfans.user'].search([
                '|', 
                ('telegram_username', '=', telegram_handle),
                ('user_id.login', '=', telegram_handle)
            ], limit=1)
            
            if user_profile:
                # Return relevant flags
                return {
                    'allowed': user_profile.allowed_url_message,
                    'name': user_profile.display_name,
                    'status': user_profile.account_status
                }
        return None

    async def link_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        user = update.effective_user
        
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
        odoo_data = self.get_odoo_user(identifier)

        # Logic: If user not found in Odoo or not allowed_url_message
        if not odoo_data or not odoo_data.get('allowed'):
            try:
                await message.delete()
                
                # Tag user in group
                bot_url = f"https://t.me/{context.bot.username}"
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("Verify Here 🚀", url=bot_url)]])
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Hey {user.mention_html()}, links are restricted! 🚫",
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )

                # Send Private Message with Web App link
                private_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Finish Task 🔗", url=self.config['WEB_APP_URL'])],
                    [InlineKeyboardButton("Back to Group", url=self.config['GROUP_LINK'])]
                ])
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"Hi {user.first_name}, please complete your profile or verification to post links.",
                    reply_markup=private_markup
                )
            except Exception as e:
                _logger.error("Bot Handler Error: %s", e)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        identifier = user.username if user.username else str(user.id)
        odoo_data = self.get_odoo_user(identifier)

        if odoo_data:
            await update.message.reply_text(f"Welcome back {odoo_data['name']}! You are linked to our platform.")
        else:
            await update.message.reply_text("Welcome! Please register on our website and add your Telegram username to your profile.")