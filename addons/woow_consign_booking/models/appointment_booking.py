# -*- coding: utf-8 -*-

from odoo import fields, models


class AppointmentBooking(models.Model):
    """Legacy consignment fields retained only for historical audit.

    Consignment redemption is intentionally no longer part of the booking
    lifecycle. Website cart checkout is the future owner of entitlement
    selection, payment validation, and redemption creation.
    """

    _inherit = 'appointment.booking'

    consign_line_id = fields.Many2one(
        'loyalty.consign.line',
        string='寄品明細',
        ondelete='set null',
        copy=False,
        help='舊版預約扣點留下的歷史關聯；新預約不再寫入。',
    )
    consign_card_id = fields.Many2one(
        'loyalty.card',
        string='寄品卡',
        related='consign_line_id.card_id',
        store=True,
    )
    consign_reserved_qty = fields.Float(
        '已預留數量',
        default=0.0,
        copy=False,
        help='舊版預約預留的歷史數值；新預約不再預留寄品數量。',
    )
    consign_redemption_id = fields.Many2one(
        'loyalty.consign.redemption',
        string='核銷單',
        ondelete='set null',
        copy=False,
        help='舊版預約扣點建立的歷史核銷單；新預約不再建立。',
    )
    consign_enabled = fields.Boolean(
        related='appointment_type_id.consign_enabled',
    )
