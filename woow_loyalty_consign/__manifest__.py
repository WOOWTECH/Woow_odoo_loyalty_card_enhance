{
    'name': '寄品卡 (Consignment Card)',
    'version': '18.0.1.0.0',
    'category': 'Sales/Loyalty',
    'summary': '延伸忠誠度模組，新增寄品卡功能：酒窖寄存、醫美療程預購等',
    'description': """
寄品卡 (Consignment Card)
=========================

延伸 Odoo 18 原生 loyalty 模組，新增 consign（寄品卡）類型。

**主要功能：**

* 會員購買商品或服務後寄存於店家，系統自動發送電子卡片通知
* 門市人員可透過掃碼或搜尋執行核銷，扣除對應數量
* 支援多種場景：
  - 酒窖寄存：整箱購入、逐瓶取用，記錄批號與儲位
  - 醫美療程：預購次數或劑量，分次施打，記錄操作師與劑量

**技術特色：**

* 繼承 loyalty.card，沿用 barcode 掃碼與 email 通知機制
* 獨立核銷單據，完整追蹤每次取用紀錄
* Portal 入口讓會員自助查閱寄品餘額
* 銷售訂單確認時可自動建立寄品卡
    """,
    'author': 'Woow Tech',
    'website': 'https://www.woow.tw',
    'license': 'LGPL-3',
    'depends': [
        'loyalty',
        'sale_loyalty',
        'pos_loyalty',
        'stock',
        'portal',
        'mail',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        'security/portal_security.xml',
        # Data
        'data/sequence_data.xml',
        'data/mail_template_data.xml',
        'data/consign_product_data.xml',
        # Views
        'views/loyalty_program_views.xml',
        'views/loyalty_card_consign_views.xml',
        'views/loyalty_consign_line_views.xml',
        'views/loyalty_consign_redemption_views.xml',
        'views/sale_order_views.xml',
        'views/menu_views.xml',
        # Wizard
        'wizard/consign_redeem_wizard_views.xml',
        # Portal
        'views/portal_templates.xml',
        # Report
        'report/consign_card_report_templates.xml',
        'report/consign_card_report_actions.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'woow_loyalty_consign/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
