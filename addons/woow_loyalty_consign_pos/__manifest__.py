{
    'name': 'Consignment Card - POS Integration',
    'version': '18.0.1.0.0',
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
        'data/consign_pos_product_data.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'woow_loyalty_consign_pos/static/src/**/*',
        ],
    },
    'installable': True,
    'auto_install': True,
}
