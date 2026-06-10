# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyConsignLine(models.Model):
    _inherit = 'loyalty.consign.line'

    # H1 fix: 加上 DB 級約束防止超額預留
    _sql_constraints = [
        ('reserved_qty_positive',
         'CHECK(reserved_qty >= 0)',
         '預留數量不可為負數。'),
    ]

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

    @api.constrains('reserved_qty')
    def _check_reserved_qty(self):
        """H1 fix: ORM 層驗證預留不超過剩餘"""
        for line in self:
            if line.reserved_qty < 0:
                raise ValidationError('預留數量不可為負數。')
