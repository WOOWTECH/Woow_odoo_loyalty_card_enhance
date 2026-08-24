# -*- coding: utf-8 -*-

from odoo import fields, models


class LoyaltyConsignLine(models.Model):
    """Legacy reservation values retained only as historical audit data.

    Appointment bookings no longer write ``reserved_qty``. Core
    ``qty_available`` is authoritative and this bridge never overrides or
    subtracts from it.
    """

    _inherit = 'loyalty.consign.line'

    reserved_qty = fields.Float(
        '預留數量',
        default=0.0,
        help='舊版預約預留的歷史數值；不再作為可用餘額的扣減依據。',
    )
