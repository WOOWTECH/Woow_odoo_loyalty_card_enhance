{
    'name': '會員中心 (Member Center)',
    'version': '18.0.2.0.0',
    'category': 'Sales/Loyalty',
    'summary': '會員中心 Portal Hub — 模組化架構，各功能可獨立安裝',
    'description': """
        會員中心 Portal Hub
        ===================
        提供統一的會員中心入口頁面，各功能模組透過 xpath 注入卡片：

        可搭配的子模組：
        - woow_mc_ewallet — 電子錢包
        - woow_mc_loyalty — 集點卡
        - woow_mc_gift_card — 禮品卡
        - woow_mc_coupon — 優惠券
        - woow_mc_consign — 寄品卡
        - woow_mc_membership — 會員資格
    """,
    'author': 'WoowTech',
    'website': 'https://www.woowtech.com',
    'depends': [
        'portal',
        'loyalty',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/portal_security.xml',
        'views/portal_templates.xml',
        'views/loyalty_history_snippet.xml',
        'views/loyalty_history_page.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'woow_member_center/static/src/css/member_center.css',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
