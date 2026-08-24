from odoo import api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyConsignRefundSaga(models.Model):
    """Durable paid-first refund coordination metadata; no channel logic yet."""

    _name = 'loyalty.consign.refund.saga'
    _description = 'Consignment Refund Saga'
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', required=True, index=True, ondelete='restrict',
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        'res.partner', required=True, index=True, ondelete='restrict',
        check_company=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('pending', 'Pending'),
            ('done', 'Done'),
            ('error', 'Error'),
            ('cancel', 'Cancelled'),
        ], required=True, default='draft', index=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_res_id = fields.Integer(required=True, index=True)
    source_name = fields.Char(required=True)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    requested_payload = fields.Json(required=True, copy=False)
    coverage_snapshot = fields.Json(copy=False)
    tax_basis_snapshot = fields.Json(copy=False)
    currency_id = fields.Many2one(
        'res.currency', required=True, index=True, ondelete='restrict',
    )
    cash_amount = fields.Monetary(
        required=True, currency_field='currency_id', default=0.0,
    )
    child_transaction_model = fields.Char(copy=False, index=True)
    child_transaction_res_id = fields.Integer(copy=False, index=True)
    child_transaction_name = fields.Char(copy=False)
    retry_count = fields.Integer(default=0, copy=False)
    next_retry_at = fields.Datetime(copy=False, index=True)
    reversal_operation_id = fields.Many2one(
        'loyalty.consign.operation', index=True, ondelete='restrict',
        check_company=True, copy=False,
    )
    error_code = fields.Char(copy=False)
    error_message = fields.Text(copy=False)
    error_metadata = fields.Json(copy=False)

    _sql_constraints = [
        (
            'company_idempotency_key_unique',
            'unique(company_id, idempotency_key)',
            'A refund saga idempotency key may only be used once per company.',
        ),
        ('source_res_id_positive', 'CHECK(source_res_id > 0)', 'The refund source must be valid.'),
        ('retry_count_nonnegative', 'CHECK(retry_count >= 0)', 'Retry count cannot be negative.'),
        ('cash_amount_nonnegative', 'CHECK(cash_amount >= 0)', 'Refund cash amount cannot be negative.'),
    ]

    @api.constrains('idempotency_key')
    def _check_idempotency_key(self):
        for saga in self:
            if not (saga.idempotency_key or '').strip():
                raise ValidationError('The refund saga idempotency key is required.')

    def unlink(self):
        raise ValidationError('Refund sagas are retained for audit and cannot be deleted.')
