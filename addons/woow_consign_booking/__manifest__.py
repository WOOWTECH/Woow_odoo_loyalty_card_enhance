{
    'name': '寄品卡預約扣點 (Consign Booking Bridge)',
    'version': '18.0.2.0.0',
    'category': 'Sales/Loyalty',
    'summary': '舊版預約寄品扣點相容層；新核銷改由電商購物車處理',
    'author': 'Woow Tech',
    'website': 'https://www.woow.tw',
    'license': 'LGPL-3',
    'depends': [
        'reservation_module',
        'woow_loyalty_consign',
    ],
    'data': [
        'views/appointment_type_views.xml',
        'views/appointment_booking_views.xml',
        'views/portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
