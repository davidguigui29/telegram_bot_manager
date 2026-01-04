from odoo import models, fields, api
from ..services.telegram_worker import TelegramBotThread

# Global variable to keep track of the running thread
BOT_THREAD = None

class TelegramConfig(models.Model):
    _name = 'telegram.config'
    _description = 'Telegram Configuration'

    name = fields.Char(default="Bot Config")
    bot_token = fields.Char(string="Token", required=True)
    group_invite_link = fields.Char()
    channel_id = fields.Char(default="@GuidasDeveloper")
    owner_id = fields.Char(string="Owner ID")
    telegram_web_app_url = fields.Char()

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


    def action_start_bot(self):
        global BOT_THREAD
        if BOT_THREAD and BOT_THREAD.is_alive():
            return  # Already running

        # Pass the database name and registry so the thread can access models
        BOT_THREAD = TelegramBotThread(
            self.env.cr.dbname,
            self.bot_token,
            {
                'GROUP_LINK': self.group_invite_link,
                'CHANNEL_ID': self.channel_id,
                'OWNER_ID': self.owner_id,
                'WEB_APP_URL': self.telegram_web_app_url,
            }
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