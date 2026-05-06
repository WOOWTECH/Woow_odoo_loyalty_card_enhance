{
    'name': '會員中心 (Member Center)',
    'version': '18.0.1.0.0',
    'category': 'Sales/Loyalty',
    'summary': '會員中心入口 — 電子錢包、集點卡、禮品卡、優惠券、會員資格、寄品卡',
    'description': """
        會員中心 Portal Hub
        ===================
        提供統一的會員中心入口頁面，整合以下功能：
        - 電子錢包 (eWallet) — 餘額查詢
        - 集點卡 (Loyalty) — 累積點數查詢
        - 禮品卡 (Gift Card) — 餘額查詢
        - 優惠券 (Coupon) — 可用券查詢
        - 會員資格 (Membership) — 狀態與期限
        - 寄品卡 (Consignment) — 連結至現有寄品卡頁面
    """,
    'author': 'WoowTech',
    'website': 'https://www.woowtech.com',
    'depends': [
        'portal',
        'loyalty',
        'membership',
        'woow_loyalty_consign',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/portal_security.xml',
        'views/portal_templates.xml',
        'views/ewallet_templates.xml',
        'views/loyalty_templates.xml',
        'views/gift_card_templates.xml',
        'views/coupon_templates.xml',
        'views/membership_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'woow_member_center/static/src/css/member_center.css',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
