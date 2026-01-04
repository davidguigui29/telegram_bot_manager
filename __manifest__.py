{
    'name': 'Telegram Bot Manager',
    'version': '1.0',
    'depends': ['base', 'myfansbook_core', 'website'], 
    'sequence': 2,
    'data': [
        'security/ir.model.access.csv',
        'views/telegram_config_views.xml',

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
    'installable': True,
    'application': True,
}