import hashlib
import hmac
import time
from odoo import http, fields, models, api
from odoo.http import request
import logging
import urllib.parse
import json
from odoo.addons.myfansbook_core.utils.helpers import reclaim_telegram_username, validate_username, validate_email as email_validator




_logger = logging.getLogger(__name__)





class TelegramAjaxAuth(http.Controller):

    @http.route('/auth_oauth/telegram/signin_ajax', type='json', auth='public', website=True, csrf=False)
    def telegram_signin_ajax(self, initData=None, **kw):
        
        if not initData:
            return {"status": "error", "message": "No data received"}

        # 1. Verification
        config = request.env['telegram.config'].sudo().search([], limit=1)
        bot_token = config.bot_token
        vals = dict(urllib.parse.parse_qsl(initData))
        received_hash = vals.pop('hash')
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(vals.items())])
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return {"status": "error", "message": "Invalid Signature"}
        

        # ... (Verification logic above) ...

        # 3. Extract Telegram User Data
        tg_user_data = json.loads(vals.get('user'))
        tg_id = str(tg_user_data.get('id'))
        tg_username = tg_user_data.get('username')

        # 4. FIND THE USER (The "Appropriate Way")
        # We search with sudo to ensure we can see across all partners
        User = request.env['res.users'].sudo()
        Partner = request.env['res.partner'].sudo()
        user = False

        # Strategy A: Check if a partner is already linked to this Telegram ID
        # (Assuming you store tg_id in a field, otherwise skip to Username)
        partner = Partner.search([('telegram_username', '=', tg_username)], limit=1)
        
        if partner:
            user = User.search([('partner_id', '=', partner.id)], limit=1)

        # Strategy B: Fallback to searching by the generic 'username' field
        if not user and tg_username:
            partner = Partner.search([('username', '=', tg_username)], limit=1)
            if partner:
                user = User.search([('partner_id', '=', partner.id)], limit=1)

        # Strategy C: If you use Phone registration, check if the tg_id matches a login
        if not user:
            user = User.search([('login', '=', tg_id)], limit=1)

        if not user:
            _logger.warning("Telegram Auth Failed: User %s not found in Odoo", tg_username or tg_id)
            return {"status": "error", "message": "User not found. Please register via the bot first."}

        # 5. AUTHENTICATION & SESSION HANDLING
        try:
            # We don't have the user's Odoo password here, so we bypass 
            # the standard password check and manually log them in
            request.session.uid = user.id
            request.session.login = user.login
            request.session.dbname = request.db
            
            # Important for Odoo 18 security
            request.session.session_token = user._compute_session_token(request.session.sid)
            request.session.modified = True 
            
            user._update_last_login()
            request.env.cr.commit()

            _logger.info("✅ [TG-AJAX] Login Successful for %s", user.login)
            
            return {
                "status": "success",
                "redirect_url": "/myfansbook/dashboard"
            }
        except Exception as e:
            _logger.error("❌ [MFB-AUTH] Handshake failed: %s", str(e))
            # return {
                
            # }
            raise

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
        # # 2. Extract Telegram User Data
        # print(f"Telegram User Data: {vals}")
        # user_data = json.loads(vals.get('user'))
        # telegram_id = str(user_data.get('id'))
        # first_name = user_data.get('first_name', 'Telegram User')
        # username = user_data.get('username', f"user_{telegram_id}")

        # # 3. Search for existing user
        # UserObj = request.env['res.users'].sudo()
        # user = UserObj.search([('oauth_uid', '=', telegram_id)], limit=1)

        # if user:
        #     mf_user = request.env['myfans.user'].sudo().search(
        #         [('user_id', '=', user.id)],
        #         limit=1
        #     )
        #     if mf_user and username:
        #         reclaim_telegram_username(
        #             request.env,
        #             username,
        #             mf_user.partner_id
        #         )


        # # --- MOVE THIS HERE so it's always available ---
        # provider = request.env['auth.oauth.provider'].sudo().search([('name', 'ilike', 'Telegram')], limit=1)

        # # 4. IF NOT FOUND -> CREATE NEW USER
        # if not user:
        #     # Check if login exists but lacks oauth_uid
        #     user = UserObj.search([('login', '=', username)], limit=1)
            
        #     if user:
        #         user.write({'oauth_uid': telegram_id})
        #     else:
        #         company = request.env['res.company'].sudo().search([('name', 'ilike', 'Myfansbook')], limit=1)
        #         if not company:
        #             company = request.env['res.company'].sudo().search([], limit=1)
                
        #         # 🟢 FIX: Use Admin user to provide a valid 'singleton' environment 
        #         # for the creation hooks (hr, mail, etc.)
        #         admin_user = request.env.ref('base.user_admin')
                
        #         # Create user using the Admin's identity
        #         user = UserObj.with_user(admin_user).with_context(
        #             no_reset_password=True, 
        #             signup_valid=False,
        #             install_mode=True,
        #             tg_username=username # Pass this for your MyFans profile logic
        #         ).create({
        #             'name': first_name,
        #             'login': username,
        #             'email': f"{username}@telegram.me",
        #             'oauth_uid': telegram_id,
        #             'oauth_provider_id': provider.id if provider else False,
        #             'company_id': company.id,
        #             'company_ids': [(4, company.id)],
        #             'groups_id': [(6, 0, [request.env.ref('base.group_portal').id])],
        #             'active': True,
        #         })
        #     # --- OWNERSHIP RECLAMATION LOGIC ---
        #     # Now that the user exists (created or found), we enforce 
        #     # that this telegram_id owns this telegram_username globally.
        #     # --- OWNERSHIP RECLAMATION LOGIC ---
        #     if username:
        #         mf_user = request.env['myfans.user'].sudo().search(
        #             [('user_id', '=', user.id)],
        #             limit=1
        #         )
        #         if mf_user:
        #             reclaim_telegram_username(
        #                 request.env,
        #                 username,
        #                 mf_user.partner_id
        #             )

 

        # # 5. Formal Odoo 18 Login (Bypassing OTP via Route Detection)
        # try:

        #     # request.session.authenticate(request.db, user.login)
        #     request.session.uid = user.id
        #     request.session.login = user.login
        #     request.session.dbname = request.db
            
        #     # Ensure the session token is set for Odoo 18 security
        #     # user_sudo = user.sudo()
        #     request.session.session_token = user.sudo()._compute_session_token(request.session.sid)
                        
        #     # Persist the session            
        #     request.session.modified = True 
        #     user.sudo().write({
        #         "login_date": fields.Datetime.now(),
        #         # "login_ip": request.httprequest.remote_addr,
        #     })
        #     user.sudo()._update_last_login()
                        
        #     request.env.cr.commit()

            
        #     _logger.info("✅ [MFB-AUTH] Formal Login Successful for %s. UI Status: Active.", user.login)
            

        # except Exception as e:
        #     _logger.error("❌ [MFB-AUTH] Handshake failed: %s", str(e))
        #     # return {
                
        #     # }
        #     raise
        #     # Fallback to manual assignment if authenticate fails for any reason
        #     # request.session.uid = user.id
        #     # request.session.login = user.login
        #     # user.sudo()._update_last_login() # Manually force the "Active" status
        #     # request.session.modified = True
        #     # request.env.cr.commit()

        # return {
        #     "status": "success",
        #     "redirect_url": "/myfansbook/dashboard"
        # }