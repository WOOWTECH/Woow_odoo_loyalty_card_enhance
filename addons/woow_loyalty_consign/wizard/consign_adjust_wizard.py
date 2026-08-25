import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ConsignAdjustWizard(models.TransientModel):
    """Manager-only, reasoned adapter for the private adjustment command."""

    _name = 'consign.adjust.wizard'
    _description = '寄品調整精靈'
    _check_company_auto = True

    card_id = fields.Many2one(
        'loyalty.card', required=True, check_company=True,
        domain=[('is_consign', '=', True)], string='寄品卡',
    )
    company_id = fields.Many2one(related='card_id.company_id', store=True)
    partner_id = fields.Many2one(related='card_id.partner_id', string='客戶')
    product_id = fields.Many2one('product.product', required=True, check_company=True, string='品項')
    product_uom_id = fields.Many2one('uom.uom', required=True, string='計量單位')
    quantity = fields.Float(required=True, string='調整數量')
    reason = fields.Text(string='調整原因')
    submission_uuid = fields.Char(
        required=True, copy=False, readonly=True, string='Submission UUID',
        default=lambda self: uuid.uuid4().hex,
    )
    operation_id = fields.Many2one(
        'loyalty.consign.operation', readonly=True, copy=False, check_company=True,
        string='Adjustment Operation',
    )

    @api.model
    def _require_manager(self):
        if not self.env.is_superuser() and not self.env.user.has_group(
            'woow_loyalty_consign.group_consign_manager'
        ):
            raise AccessError('Only Consign Managers may execute manual adjustments.')

    @api.model_create_multi
    def create(self, vals_list):
        self._require_manager()
        return super().create(vals_list)

    def write(self, vals):
        self._require_manager()
        return super().write(vals)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id

    def action_confirm(self):
        self.ensure_one()
        self._require_manager()
        if self.operation_id:
            return self._operation_action(self.operation_id)
        if not (self.reason or '').strip():
            raise ValidationError('An adjustment reason is required.')
        if not (self.submission_uuid or '').strip():
            raise ValidationError('A stable submission UUID is required.')
        operation = self.env['loyalty.consign.engine']._adjust(
            source=self, card=self.card_id, product=self.product_id,
            uom=self.product_uom_id, quantity=self.quantity, reason=self.reason,
            idempotency_key=f'consign:backend:adjust:v1:{self.submission_uuid}',
        )
        self.write({'operation_id': operation.id})
        return self._operation_action(operation)

    def _operation_action(self, operation):
        return {
            'name': 'Consignment Adjustment', 'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.operation', 'res_id': operation.id,
            'view_mode': 'form', 'target': 'current',
        }
