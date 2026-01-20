# Telegram Bot Manager (Odoo addon)

Manage a Telegram bot from Odoo, including user onboarding and account linking, channel/group gating, and a Telegram WebApp login handshake. The addon runs a background polling thread for the bot and exposes a Telegram WebApp login endpoint for your website.

## What it does
- Starts/stops a Telegram bot from Odoo with a single config record
- Handles registration via Telegram chat (email or phone) with OTP email verification
- Links existing Odoo users to Telegram accounts
- Checks channel membership before granting group access
- Welcomes new group members and guides them to register or join the channel
- Provides admin-only commands to post/pin a welcome button and clear recent messages
- Injects a Telegram WebApp login button into the Odoo auth OAuth providers view and validates signed initData via JSON route

## Key features
- Background bot thread with start/stop actions and auto-start on Odoo boot
- Allowed-command filtering for unknown commands
- Website login via Telegram WebApp signature verification
- Bot management UI under MyTelegram > Bot Management > Bot Settings

## Dependencies
- Odoo addons: `base`, `myfansbook_core`, `website`, `auth_signup`, `auth_oauth`
- Python: `telegram` (python-telegram-bot), `httpx`

## Installation
1. Add the addon to your Odoo addons path.
2. Install the `telegram_bot_manager` module.
3. Ensure the bot is an admin in the target channel and group.

## Configuration
Create a Telegram Bot Configuration record in Odoo:
- Token: bot token from BotFather
- Channel ID: e.g., `@yourchannel`
- Channel/Group links and invite links
- Bot Inbox URL: deep link to the bot (sends users to the bot private chat)
- Dashboard URL: website dashboard for browser login
- Telegram Web App URL: URL of the Telegram mini app
- Website Name: display name in messages
- Allowed Commands: comma-separated list (default: `start,setup_post,hello`)
- Auto-start on Boot: start the bot when the Odoo registry loads (single-worker only)

Note: Auto-start is skipped when Odoo is running with multiple workers (workers != 0).

## Usage
- Start/Stop the bot from the configuration form or list view.
- Users can register or link accounts in a private chat with the bot.
- The bot checks if users are in the required channel before granting group features.
- Admin commands:
  - `/setup_post` posts and pins a welcome button in the channel
  - `/clear` deletes recent messages in a group (admin only)
  - `/hello` sends a greeting

## Web login (Telegram WebApp)
The addon injects a Telegram login button on the OAuth providers page and sends Telegram `initData` to:
- `POST /auth_oauth/telegram/signin_ajax`

The endpoint validates the signature using your bot token and logs the user into Odoo if a matching user/partner is found.

## Security & access
- Only system users (base.group_system) can manage the bot configuration model.

## Files of interest
- `models/telegram_config.py`: configuration model and start/stop actions
- `services/telegram_worker.py`: main bot logic and handlers
- `controllers/main.py`: Telegram WebApp login endpoint
- `views/telegram_config_views.xml`: Odoo UI
- `views/auth_oauth_views.xml`: login button injection

## Notes
- The bot uses polling and runs in a background thread; keep Odoo logs for troubleshooting.
- The module expects fields on partner/user profiles used by `myfansbook_core` (telegram_id, telegram_username, etc.).
