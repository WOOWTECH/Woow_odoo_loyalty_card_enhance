# -*- coding: utf-8 -*-

from odoo import fields, models


class AppointmentType(models.Model):
    """Legacy booking-side consignment configuration.

    These fields remain so existing records can be read during the transition.
    They no longer affect booking creation, confirmation, cancellation, or
    payment configuration. Future consignment redemption belongs to website
    cart checkout.
    """

    _inherit = 'appointment.type'

    consign_enabled = fields.Boolean(
        '啟用寄品扣點',
        help='舊版預約扣點設定；已停用，請改由電商購物車核銷。',
    )
    consign_program_id = fields.Many2one(
        'loyalty.program',
        string='寄品方案',
        domain=[('program_type', '=', 'consign'), ('active', '=', True)],
    )
    consign_product_id = fields.Many2one(
        'product.product',
        string='扣點品項',
    )
    consign_qty = fields.Float(
        '每次扣點數量',
        default=1.0,
        help='舊版預約扣點數量；新預約不再使用。',
    )
