# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AppointmentType(models.Model):
    _inherit = 'appointment.type'

    # M3 fix: DB 級互斥約束，防止 SQL 繞過
    _sql_constraints = [
        ('payment_consign_exclusive',
         'CHECK(NOT (require_payment = TRUE AND consign_enabled = TRUE))',
         '「需要付款」與「啟用寄品扣點」不可同時啟用。'),
    ]

    consign_enabled = fields.Boolean(
        '啟用寄品扣點',
        help='啟用後，建立預約時會從客戶寄品卡扣除點數',
    )
    consign_program_id = fields.Many2one(
        'loyalty.program',
        string='寄品方案',
        domain=[('program_type', '=', 'consign'), ('active', '=', True)],
    )
    consign_product_id = fields.Many2one(
        'product.product',
        string='扣點品項',
        help='從寄品卡扣除的品項（需與寄品明細的品項一致）',
    )
    consign_qty = fields.Float(
        '每次扣點數量',
        default=1.0,
        help='每次預約從寄品卡扣除的數量',
    )

    @api.constrains('require_payment', 'consign_enabled')
    def _check_payment_consign_exclusive(self):
        for rec in self:
            if rec.require_payment and rec.consign_enabled:
                raise ValidationError(
                    '「需要付款」與「啟用寄品扣點」不可同時啟用。'
                    '寄品卡本身即為預付款機制。'
                )

    @api.constrains('consign_enabled', 'consign_program_id', 'consign_product_id')
    def _check_consign_configuration(self):
        for rec in self:
            if rec.consign_enabled:
                if not rec.consign_program_id:
                    raise ValidationError('啟用寄品扣點時，必須選擇寄品方案。')
                if not rec.consign_product_id:
                    raise ValidationError('啟用寄品扣點時，必須選擇扣點品項。')
                if rec.consign_qty <= 0:
                    raise ValidationError('每次扣點數量必須大於 0。')
