import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ConsignRedeemWizard(models.TransientModel):
    """Manager UI adapter for one idempotent authorize/capture command."""

    _name = 'consign.redeem.wizard'
    _description = '寄品核銷精靈'
    _check_company_auto = True

    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True, check_company=True,
        domain=[('is_consign', '=', True)],
    )
    company_id = fields.Many2one(related='card_id.company_id', store=True)
    partner_id = fields.Many2one(related='card_id.partner_id', string='客戶')
    date_redemption = fields.Datetime(string='核銷日期', default=fields.Datetime.now)
    staff_user_id = fields.Many2one('res.users', string='服務人員', default=lambda self: self.env.user)
    service_note = fields.Text(string='服務備註')
    submission_uuid = fields.Char(
        string='Submission UUID', required=True, copy=False, readonly=True,
        default=lambda self: uuid.uuid4().hex,
    )
    redemption_id = fields.Many2one(
        'loyalty.consign.redemption', string='核銷單', readonly=True, copy=False,
        check_company=True,
    )
    line_ids = fields.One2many('consign.redeem.wizard.line', 'wizard_id', string='核銷明細')

    @api.model
    def _require_manager(self):
        if not self.env.is_superuser() and not self.env.user.has_group(
            'woow_loyalty_consign.group_consign_manager'
        ):
            raise AccessError('Only Consign Managers may execute manual redemptions.')

    @api.model_create_multi
    def create(self, vals_list):
        self._require_manager()
        return super().create(vals_list)

    def write(self, vals):
        self._require_manager()
        return super().write(vals)

    @api.onchange('card_id')
    def _onchange_card_id(self):
        if self.card_id:
            active_lines = self.card_id.consign_line_ids.filtered(lambda line: line.state == 'active')
            self.line_ids = [(5, 0, 0)] + [(0, 0, {
                'consign_line_id': line.id, 'selected': False, 'qty_to_redeem': 0,
            }) for line in active_lines]

    def _document_values(self, selected_lines):
        return {
            'card_id': self.card_id.id,
            'date_redemption': self.date_redemption,
            'staff_user_id': self.staff_user_id.id,
            'service_note': self.service_note,
            'submission_uuid': self.submission_uuid,
            'line_ids': [(0, 0, {
                'consign_line_id': line.consign_line_id.id,
                'qty_redeemed': line.qty_to_redeem,
                'note': line.note,
            }) for line in selected_lines],
        }

    def action_confirm(self):
        self.ensure_one()
        self._require_manager()
        if self.redemption_id:
            return self._document_action(self.redemption_id)
        if not (self.service_note or '').strip():
            raise ValidationError('A service note/reason is required before capture.')
        if not (self.submission_uuid or '').strip():
            raise ValidationError('A stable submission UUID is required before capture.')
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise ValidationError('請至少勾選一筆品項進行核銷。')
        existing = self.env['loyalty.consign.redemption'].search([
            ('company_id', '=', self.company_id.id),
            ('submission_uuid', '=', self.submission_uuid),
        ], limit=1)
        document = existing or self.env['loyalty.consign.redemption'].create(
            self._document_values(selected_lines)
        )
        if document.card_id != self.card_id:
            raise ValidationError('The submission UUID already belongs to another card.')
        document.action_done()
        self.write({'redemption_id': document.id})
        return self._document_action(document)

    def _document_action(self, document):
        return {
            'name': '核銷單', 'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.redemption', 'res_id': document.id,
            'view_mode': 'form', 'target': 'current',
        }


class ConsignRedeemWizardLine(models.TransientModel):
    _name = 'consign.redeem.wizard.line'
    _description = '寄品核銷精靈明細'

    wizard_id = fields.Many2one('consign.redeem.wizard', string='精靈', ondelete='cascade')
    consign_line_id = fields.Many2one('loyalty.consign.line', string='寄品明細')
    product_id = fields.Many2one(related='consign_line_id.product_id', string='品項')
    product_desc = fields.Char(related='consign_line_id.product_desc', string='品項說明')
    qty_available = fields.Float(related='consign_line_id.qty_available', string='可用數量')
    selected = fields.Boolean(string='勾選', default=False)
    qty_to_redeem = fields.Float(string='核銷數量')
    note = fields.Char(string='備註')

    @api.onchange('selected')
    def _onchange_selected(self):
        if self.selected and not self.qty_to_redeem:
            self.qty_to_redeem = 1.0
        elif not self.selected:
            self.qty_to_redeem = 0
