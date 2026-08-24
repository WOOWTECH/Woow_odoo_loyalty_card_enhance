import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyConsignOperation(models.Model):
    """Durable command journal used by the future authoritative engine."""

    _name = 'loyalty.consign.operation'
    _description = 'Consignment Operation Journal'
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
    operation_type = fields.Selection(
        [
            ('issue', 'Issue'),
            ('authorize', 'Authorize'),
            ('capture', 'Capture'),
            ('release', 'Release'),
            ('reverse', 'Reverse'),
            ('adjust', 'Adjust'),
        ], required=True, index=True,
    )
    state = fields.Selection(
        [('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed')],
        required=True, default='pending', index=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_res_id = fields.Integer(required=True, index=True)
    source_name = fields.Char(required=True)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    payload_hash = fields.Char(required=True, size=64, copy=False)
    payload_json = fields.Json(copy=False)
    result_json = fields.Json(copy=False)
    completed_at = fields.Datetime(copy=False)
    error_code = fields.Char(copy=False)
    error_name = fields.Char(copy=False)
    error_message = fields.Text(copy=False)
    error_metadata = fields.Json(copy=False)
    movement_ids = fields.One2many(
        'loyalty.consign.movement', 'operation_id', copy=False,
    )
    hold_ids = fields.One2many(
        'loyalty.consign.hold', 'operation_id', copy=False,
    )

    _sql_constraints = [
        (
            'company_idempotency_key_unique',
            'unique(company_id, idempotency_key)',
            'An idempotency key may only be used once per company.',
        ),
        (
            'source_res_id_positive',
            'CHECK(source_res_id > 0)',
            'The operation source record must be valid.',
        ),
    ]

    @api.model
    def _canonical_payload(self, payload):
        """Return stable JSON and SHA-256 for already-normalized primitives."""
        canonical = json.dumps(
            payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        )
        return canonical, hashlib.sha256(canonical.encode()).hexdigest()

    @api.model
    def _lock_idempotency_key(self, company_id, idempotency_key):
        # Two-int transaction lock keeps company scopes independent. Hash
        # collisions only serialize unrelated commands and cannot corrupt data.
        digest = hashlib.sha256(idempotency_key.encode()).digest()
        key_token = int.from_bytes(digest[:4], 'big', signed=True)
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)',
            (company_id, key_token),
        )

    @api.model
    def _touch_company_serialization_token(self, company_id):
        """Fence stale REPEATABLE READ snapshots after the per-key lock.

        Task 3 deliberately uses the durable company tuple, which serializes
        command opening per company. Task 4 can replace it with a per-key
        durable token without changing the advisory-lock or unique-constraint
        guards.
        """
        self.env.cr.execute(
            '''
                UPDATE res_company
                   SET write_date = write_date
                 WHERE id = %s
             RETURNING id
            ''',
            (company_id,),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('The operation company no longer exists.')

    @api.model
    def _open_command(
        self, *, operation_type, company, partner, source_model,
        source_res_id, source_name, idempotency_key, payload,
    ):
        """Serialize and journal a command, returning ``(record, replay)``."""
        if not (idempotency_key or '').strip():
            raise ValidationError('The idempotency key is required.')
        company = company if hasattr(company, 'id') else self.env['res.company'].browse(company)
        partner = partner if hasattr(partner, 'id') else self.env['res.partner'].browse(partner)
        if not company.exists() or not partner.exists():
            raise ValidationError('The operation company and partner are required.')
        if partner.company_id and partner.company_id != company:
            raise ValidationError('The operation partner belongs to another company.')
        envelope = {
            'operation_type': operation_type,
            'partner_id': partner.id,
            'source_model': source_model,
            'source_res_id': source_res_id,
            'source_name': source_name,
            'payload': payload,
        }
        _canonical, payload_hash = self._canonical_payload(envelope)
        self._lock_idempotency_key(company.id, idempotency_key)
        # The tuple update must follow the advisory lock and precede the
        # journal search. If a winner committed after this transaction's
        # snapshot, PostgreSQL raises SerializationFailure here; Odoo retries
        # the request with a fresh snapshot. Never catch it in this method.
        self._touch_company_serialization_token(company.id)
        operation = self.sudo().search([
            ('company_id', '=', company.id),
            ('idempotency_key', '=', idempotency_key),
        ], limit=1)
        if operation:
            if operation.payload_hash != payload_hash:
                raise ValidationError(
                    'The idempotency key was already used with a different payload.'
                )
            return operation, True
        operation = self.sudo().create({
            'company_id': company.id,
            'partner_id': partner.id,
            'operation_type': operation_type,
            'source_model': source_model,
            'source_res_id': source_res_id,
            'source_name': source_name,
            'idempotency_key': idempotency_key,
            'payload_hash': payload_hash,
            'payload_json': envelope,
        })
        return operation, False

    @api.constrains('idempotency_key', 'payload_hash')
    def _check_canonical_metadata(self):
        for operation in self:
            if not (operation.idempotency_key or '').strip():
                raise ValidationError('The idempotency key is required.')
            if len(operation.payload_hash or '') != 64:
                raise ValidationError('The canonical payload hash must be SHA-256.')

    def unlink(self):
        raise ValidationError('Consignment operation journals cannot be deleted.')
