import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


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

    @api.model
    def _canonical_payload(self, payload):
        """Return a stable replay identity without trusting caller ordering."""
        return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

    @api.model
    def _open_refund(self, source, partner, currency, idempotency_key, cash_amount, reversals):
        """Open or exactly replay a trusted refund request.

        ``reversals`` is deliberately a compact server-side snapshot: each row
        names an original posted movement and the exact quantity to reverse once
        the payment adapter reports terminal ``done``.  Browser/controller input
        must never call this method directly.
        """
        if not isinstance(source, models.BaseModel):
            raise ValidationError('The refund source must be a trusted record.')
        source.ensure_one()
        partner.ensure_one()
        currency.ensure_one()
        company = source.company_id if 'company_id' in source._fields else False
        if not company or partner.company_id and partner.company_id != company:
            raise ValidationError('The refund source and customer must belong to one company.')
        if not idempotency_key or not idempotency_key.strip():
            raise ValidationError('A refund idempotency key is required.')
        if cash_amount < 0:
            raise ValidationError('Refund cash amount cannot be negative.')
        normalized = []
        for item in reversals or []:
            if not isinstance(item, dict):
                raise ValidationError('Every refund reversal must be a mapping.')
            movement = self.env['loyalty.consign.movement'].browse(item.get('movement_id'))
            movement.ensure_one()
            if not movement.exists() or movement.company_id != company:
                raise ValidationError('A refund reversal movement is invalid for this company.')
            kind = item.get('kind')
            if kind not in ('redeem', 'issue') or movement.movement_type != kind:
                raise ValidationError('A refund reversal must name its exact original movement type.')
            quantity = float(item.get('quantity', 0.0))
            if float_compare(quantity, 0.0, precision_rounding=movement.product_uom_id.rounding) <= 0:
                raise ValidationError('A refund reversal quantity must be positive.')
            normalized.append({
                'kind': kind, 'movement_id': movement.id, 'quantity': quantity,
            })
        normalized.sort(key=lambda item: (item['kind'], item['movement_id']))
        payload = {
            'source_model': source._name, 'source_res_id': source.id,
            'partner_id': partner.id, 'currency_id': currency.id,
            'cash_amount': cash_amount, 'reversals': normalized,
        }
        canonical = self._canonical_payload(payload)
        existing = self.sudo().search([
            ('company_id', '=', company.id), ('idempotency_key', '=', idempotency_key),
        ], limit=1)
        if existing:
            if self._canonical_payload(existing.requested_payload) != canonical:
                raise ValidationError('The refund idempotency key was already used with a different payload.')
            return existing
        return self.sudo().create({
            'company_id': company.id,
            'partner_id': partner.id,
            'currency_id': currency.id,
            'source_model': source._name,
            'source_res_id': source.id,
            'source_name': source.display_name,
            'idempotency_key': idempotency_key,
            'requested_payload': payload,
            'coverage_snapshot': {'reversals': normalized},
            'cash_amount': cash_amount,
            'state': 'pending',
        })

    def _payment_callback(self, payment_state, child_transaction=False):
        """Advance one refund saga; only terminal done restores entitlement."""
        self.ensure_one()
        if payment_state not in ('pending', 'done', 'error', 'cancel'):
            raise ValidationError('The refund payment state is invalid.')
        if child_transaction:
            child_transaction.ensure_one()
            self.write({
                'child_transaction_model': child_transaction._name,
                'child_transaction_res_id': child_transaction.id,
                'child_transaction_name': child_transaction.display_name,
            })
        if self.state == 'done':
            if payment_state == 'done':
                return self
            raise ValidationError('A completed refund saga cannot change payment state.')
        if payment_state != 'done':
            self.write({'state': payment_state})
            return self
        source = self.env[self.source_model].browse(self.source_res_id).exists()
        if not source:
            raise ValidationError('The refund source no longer exists.')
        engine = self.env['loyalty.consign.engine']
        operations = self.env['loyalty.consign.operation']
        for item in self.coverage_snapshot.get('reversals', []):
            movement = self.env['loyalty.consign.movement'].browse(item['movement_id']).exists()
            if not movement:
                raise ValidationError('The original movement no longer exists.')
            key = '%s:%s:%s' % (self.idempotency_key, item['kind'], movement.id)
            if item['kind'] == 'redeem':
                operation = engine._reverse_redeem(
                    source, self.partner_id, movement, item['quantity'], key,
                )
            else:
                operation = engine._clawback_issue(
                    source, self.partner_id, movement, item['quantity'], key,
                )
            operations |= operation
        self.write({
            'state': 'done',
            'reversal_operation_id': operations[:1].id if operations else False,
            'error_code': False, 'error_message': False, 'error_metadata': False,
        })
        return self
