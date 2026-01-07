{
    'name': 'Telegram Bot Manager',
    'version': '1.0',
    'depends': ['base', 'myfansbook_core', 'website', 'auth_signup', 'auth_oauth'], 
    'sequence': 2,
    'data': [
        'security/ir.model.access.csv',
        'views/telegram_config_views.xml',
        "views/auth_oauth_views.xml",
        # 'data/auth_oauth_provider_telegram.xml',

    ],


    'assets': {
        'web.assets_backend': [
            'telegram_bot_manager/static/src/js/field_widget.js',
            'telegram_bot_manager/static/src/xml/field_widget.xml',

            
        ],
    },

    'external_dependencies': {
        'python': ['telegram', 'httpx'],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}