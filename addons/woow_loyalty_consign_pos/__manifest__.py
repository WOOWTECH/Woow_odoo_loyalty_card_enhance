{
    'name': 'Consignment Card - POS Integration',
    'version': '18.0.1.0.1',
    'category': 'Sales/Point of Sale',
    'summary': 'POS barcode scan and manual redemption for consignment cards',
    'author': 'Woow Tech',
    'website': 'https://www.woow.tw',
    'license': 'LGPL-3',
    'depends': [
        'woow_loyalty_consign',
        'pos_loyalty',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/consign_pos_product_data.xml',
        'views/pos_consign_menu_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'woow_loyalty_consign_pos/static/src/**/*',
        ],
    },
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'auto_install': True,
}
