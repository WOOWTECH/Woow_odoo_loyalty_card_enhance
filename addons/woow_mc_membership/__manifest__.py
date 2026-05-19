{
    'name': '會員中心 - 會員資格',
    'version': '18.0.1.0.0',
    'category': 'Sales/Loyalty',
    'summary': '會員中心會員資格功能模組',
    'author': 'WoowTech',
    'website': 'https://www.woowtech.com',
    'depends': [
        'woow_member_center',
        'membership',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/portal_security.xml',
        'views/portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
