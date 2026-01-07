from odoo import models, fields, api, tools
from ..services.telegram_worker import TelegramBotThread
from odoo.http import request
import logging
_logger = logging.getLogger(__name__)

# Global variable to keep track of the running thread
BOT_THREAD = None

class TelegramConfig(models.Model):
    _name = 'telegram.config'
    _description = 'Telegram Configuration'

    auto_start = fields.Boolean(string="Auto-start on Boot", default=True)

    name = fields.Char(default="Bot Config")
    bot_token = fields.Char(string="Token", required=True)
    channel_link = fields.Char(string="Channel Link", default="https://t.me/GuidasDeveloper")
    group_invite_link = fields.Char(string="Group Invite Link", default="https://t.me/GuidasDeveloper")
    group_link = fields.Char(string="Group Link", default="https://t.me/GuidasDeveloper")
    channel_id = fields.Char(string="Channel ID", default="@GuidasDeveloper")
    owner_id = fields.Char(string="Owner ID")
    telegram_web_app_url = fields.Char(string="Telegram Web App URL")
    website_name = fields.Char(string="Website Name", default="Myfansbook")
    log_message_id = fields.Char(string="Log File Name", default="message_id.txt")
    allowed_commands = fields.Char(
        string="Allowed Commands", 
        default="start,setup_post,hello",
        help="Comma-separated list of allowed commands"
    )



    bot_running = fields.Boolean(
        string="Bot Status", 
        compute="_compute_bot_running", 
        help="Indicates if the background bot thread is currently active."
    )

    def _compute_bot_running(self):
        """ Checks if the global bot thread is alive """
        global BOT_THREAD
        status = bool(BOT_THREAD and BOT_THREAD.is_alive())
        for record in self:
            record.bot_running = status

    bot_status = fields.Selection([
        ('running', 'Live'),
        ('stopped', 'Offline')
    ], compute="_compute_bot_status", string="Status")

    def _compute_bot_status(self):
        global BOT_THREAD
        is_alive = bool(BOT_THREAD and BOT_THREAD.is_alive())
        for record in self:
            record.bot_status = 'running' if is_alive else 'stopped'



    def _register_hook(self):
        """ 
        Odoo calls this method after the registry is fully loaded.
        We use it to auto-start the bot thread if a configuration exists.
        """
        super(TelegramConfig, self)._register_hook()

        if tools.config['workers'] != 0:
            return
        # Only start configurations marked for auto-start
        # configs = self.env['telegram.config'].search([('auto_start', '=', True)])
        configs = self.env['telegram.config'].sudo().search([('auto_start', '=', True)])
        for config in configs:
            _logger.info("Auto-starting Telegram Bot during Odoo startup...")
            _logger.info("Auto-starting Telegram Bot: %s", config.name)
            config.action_start_bot()



    def action_start_bot(self):
        global BOT_THREAD
        if BOT_THREAD and BOT_THREAD.is_alive():
            return  # Already running


        # Build the config dictionary to pass to the thread
        config_data = {
            'CHANNEL_LINK': self.channel_link,
            'GROUP_LINK': self.group_invite_link,
            'CHANNEL_ID': self.channel_id,
            'OWNER_ID': self.owner_id,
            'TELEGRAM_WEB_APP_URL': self.telegram_web_app_url,
            'WEBSITE_NAME': self.website_name,
            'LOG_FILE': self.log_message_id,
            'ALLOWED_COMMANDS': [cmd.strip() for cmd in self.allowed_commands.split(',')]
        }
        # Pass the database name and registry so the thread can access models
        BOT_THREAD = TelegramBotThread(
            self.env.cr.dbname,
            self.bot_token,
            config_data
        )
        BOT_THREAD.start()

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }


    def action_stop_bot(self):
        global BOT_THREAD
        if BOT_THREAD:
            try:
                # Call the updated stop method
                BOT_THREAD.stop_polling()
                
                # Wait for thread to finish for a max of 5 seconds
                BOT_THREAD.join(timeout=5)
                
                if BOT_THREAD.is_alive():
                    _logger.warning("Telegram thread did not exit cleanly, forcing cleanup.")
            except Exception as e:
                _logger.error(f"Error stopping bot thread: {e}")
            finally:
                # ALWAYS set to None so the 'Start' button becomes available again
                BOT_THREAD = None
        
        # Trigger a UI refresh to update the status badge
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
    # def action_stop_bot(self):
    #     global BOT_THREAD
    #     if BOT_THREAD:
    #         try:
    #             BOT_THREAD.stop_polling()
    #             # Join with a timeout to ensure the thread closes without hanging Odoo
    #             BOT_THREAD.join(timeout=5)
    #         except Exception as e:
    #             _logger.error(f"Error stopping bot thread: {e}")
    #         finally:
    #             BOT_THREAD = None
    #     return True

