# -*- coding: utf-8 -*-
from odoo import api, fields, models


class LoyaltyConsignLine(models.Model):
    _inherit = 'loyalty.consign.line'

    reserved_qty = fields.Float(
        '預留數量',
        default=0.0,
        help='已被預約預留但尚未核銷的數量',
    )
    qty_available = fields.Float(
        '可用數量',
        compute='_compute_qty_available',
        store=True,
        help='剩餘數量減去預留數量',
    )

    @api.depends('qty_remaining', 'reserved_qty')
    def _compute_qty_available(self):
        for line in self:
            line.qty_available = max(0.0, line.qty_remaining - line.reserved_qty)
