# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    consign_card_count = fields.Integer(
        string='寄品卡', compute='_compute_consign_card_count',
    )

    def _compute_consign_card_count(self):
        for partner in self:
            partner.consign_card_count = self.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('program_id.program_type', '=', 'consign'),
            ])

    def action_view_consign_cards(self):
        """Smart button: open consign cards for this partner."""
        self.ensure_one()
        cards = self.env['loyalty.card'].sudo().search([
            ('partner_id', '=', self.id),
            ('program_id.program_type', '=', 'consign'),
        ])
        action = {
            'type': 'ir.actions.act_window',
            'name': '寄品卡',
            'res_model': 'loyalty.card',
            'view_mode': 'list,form',
            'domain': [('id', 'in', cards.ids)],
        }
        if len(cards) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = cards.id
        return action
