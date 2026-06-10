{
    'name': '寄品卡預約扣點 (Consign Booking Bridge)',
    'version': '18.0.1.0.0',
    'category': 'Sales/Loyalty',
    'summary': '預約系統與寄品卡整合：預約時檢查餘額、預留點數、確認時扣除',
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
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
