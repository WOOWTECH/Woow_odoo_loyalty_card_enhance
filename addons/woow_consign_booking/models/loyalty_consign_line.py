# -*- coding: utf-8 -*-

from odoo import api, fields, models


class LoyaltyConsignLine(models.Model):
    """Legacy reservation values retained for audit during the transition.

    Appointment bookings no longer write ``reserved_qty``. The value is not a
    balance hold and is excluded from all active redemption paths.
    """

    _inherit = 'loyalty.consign.line'

    reserved_qty = fields.Float(
        '預留數量',
        default=0.0,
        help='舊版預約預留的歷史數值；不再作為可用餘額的扣減依據。',
    )
    qty_available = fields.Float(
        '可用數量',
        compute='_compute_qty_available',
        store=True,
        help='舊版欄位，僅供歷史查閱；現行核銷應以剩餘數量為準。',
    )

    @api.depends('qty_remaining', 'reserved_qty')
    def _compute_qty_available(self):
        for line in self:
            line.qty_available = max(0.0, line.qty_remaining - line.reserved_qty)
