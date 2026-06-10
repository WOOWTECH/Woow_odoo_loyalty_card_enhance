# -*- coding: utf-8 -*-
from odoo import fields, models


class LoyaltyConsignRedemption(models.Model):
    _inherit = 'loyalty.consign.redemption'

    booking_id = fields.Many2one(
        'appointment.booking',
        string='關聯預約',
        ondelete='set null',
        help='由預約扣點自動建立時，關聯的預約單',
    )
