from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ConsignRedeemWizard(models.TransientModel):
    _name = 'consign.redeem.wizard'
    _description = '寄品核銷精靈'

    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True,
        domain=[('is_consign', '=', True)],
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', string='客戶',
    )
    date_redemption = fields.Datetime(
        string='核銷日期', default=fields.Datetime.now,
    )
    staff_user_id = fields.Many2one(
        'res.users', string='服務人員', default=lambda self: self.env.user,
    )
    service_note = fields.Text(
        string='服務備註',
    )
    line_ids = fields.One2many(
        'consign.redeem.wizard.line', 'wizard_id', string='核銷明細',
    )

    @api.onchange('card_id')
    def _onchange_card_id(self):
        if self.card_id:
            active_lines = self.card_id.consign_line_ids.filtered(
                lambda l: l.state == 'active'
            )
            wizard_lines = []
            for cl in active_lines:
                wizard_lines.append((0, 0, {
                    'consign_line_id': cl.id,
                    'selected': False,
                    'qty_to_redeem': 0,
                }))
            self.line_ids = [(5, 0, 0)] + wizard_lines

    def action_confirm(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise ValidationError('請至少勾選一筆品項進行核銷。')

        for wl in selected_lines:
            cl = wl.consign_line_id
            desc = cl.product_desc or cl.product_id.name
            available = cl.qty_remaining
            if wl.qty_to_redeem <= 0:
                raise ValidationError(
                    f'品項「{desc}」的核銷數量必須大於 0。'
                )
            if wl.qty_to_redeem > available:
                raise ValidationError(
                    f'品項「{desc}」'
                    f'核銷數量 ({wl.qty_to_redeem}) 超過可用數量 ({available})。'
                )

        redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': self.card_id.id,
            'date_redemption': self.date_redemption,
            'staff_user_id': self.staff_user_id.id,
            'service_note': self.service_note,
            'line_ids': [(0, 0, {
                'consign_line_id': wl.consign_line_id.id,
                'qty_redeemed': wl.qty_to_redeem,
                'note': wl.note,
            }) for wl in selected_lines],
        })
        redemption.action_done()

        return {
            'name': '核銷單',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.redemption',
            'res_id': redemption.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ConsignRedeemWizardLine(models.TransientModel):
    _name = 'consign.redeem.wizard.line'
    _description = '寄品核銷精靈明細'

    wizard_id = fields.Many2one(
        'consign.redeem.wizard', string='精靈', ondelete='cascade',
    )
    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='寄品明細',
    )
    product_id = fields.Many2one(
        related='consign_line_id.product_id', string='品項',
    )
    product_desc = fields.Char(
        related='consign_line_id.product_desc', string='品項說明',
    )
    qty_available = fields.Float(
        related='consign_line_id.qty_remaining', string='可用數量',
    )
    selected = fields.Boolean(
        string='勾選', default=False,
    )
    qty_to_redeem = fields.Float(
        string='核銷數量',
    )
    note = fields.Char(
        string='備註',
    )

    @api.onchange('selected')
    def _onchange_selected(self):
        if self.selected and not self.qty_to_redeem:
            self.qty_to_redeem = 1.0
        elif not self.selected:
            self.qty_to_redeem = 0
