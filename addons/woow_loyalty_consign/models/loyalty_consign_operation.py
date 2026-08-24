import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


_OPERATION_CONTEXT_KEY = '_woow_consign_operation_mutation_token'
_OPERATION_TOKEN = object()


class LoyaltyConsignOperationToken(models.Model):
    """Durable stale-snapshot fence scoped to one command key."""

    _name = 'loyalty.consign.operation.token'
    _description = 'Consignment Operation Idempotency Token'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', required=True, index=True, ondelete='cascade',
    )
    idempotency_key = fields.Char(required=True, index=True, copy=False)

    _sql_constraints = [
        (
            'company_idempotency_key_unique',
            'unique(company_id, idempotency_key)',
            'Only one durable token is allowed per company and command key.',
        ),
    ]


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
    def _touch_idempotency_serialization_token(
        self, company_id, idempotency_key,
    ):
        """Fence stale snapshots without serializing distinct command keys."""
        self.env.cr.execute(
            '''
                INSERT INTO loyalty_consign_operation_token
                            (company_id, idempotency_key, create_uid, write_uid,
                             create_date, write_date)
                     VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (company_id, idempotency_key)
                DO UPDATE SET write_date = loyalty_consign_operation_token.write_date
                RETURNING id
            ''',
            (
                company_id, idempotency_key, self.env.uid, self.env.uid,
            ),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('The operation idempotency token is unavailable.')

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(_OPERATION_CONTEXT_KEY) is not _OPERATION_TOKEN:
            raise ValidationError(
                'Consignment operations can only be created by the private engine.'
            )
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get(_OPERATION_CONTEXT_KEY) is not _OPERATION_TOKEN:
            raise ValidationError(
                'Consignment operations can only be completed by the private engine.'
            )
        return super().write(vals)

    @api.model
    def _open_command(
        self, *, operation_type, company, partner, source_model,
        source_res_id, source_name, idempotency_key, payload,
        identity_payload=None,
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
        identity_envelope = {
            'operation_type': operation_type,
            'partner_id': partner.id,
            'source_model': source_model,
            'source_res_id': source_res_id,
            'payload': payload if identity_payload is None else identity_payload,
        }
        _canonical, payload_hash = self._canonical_payload(identity_envelope)
        self._lock_idempotency_key(company.id, idempotency_key)
        # The durable per-key upsert follows the matching advisory lock and
        # precedes journal search. A stale same-key waiter receives
        # SerializationFailure; distinct keys never touch the same tuple.
        self._touch_idempotency_serialization_token(
            company.id, idempotency_key,
        )
        operation = self.sudo().search([
            ('company_id', '=', company.id),
            ('idempotency_key', '=', idempotency_key),
        ], limit=1)
        if operation:
            if operation.payload_hash != payload_hash:
                raise ValidationError(
                    'The idempotency key was already used with a different payload.'
                )
            return self.browse(operation.ids), True
        operation = self.with_context(**{
            _OPERATION_CONTEXT_KEY: _OPERATION_TOKEN,
        }).sudo().create({
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
        return self.browse(operation.ids), False

    def _complete_from_engine(self, result):
        self.ensure_one()
        if self.state != 'pending':
            raise ValidationError('Only a pending consignment command can be completed.')
        self.with_context(**{
            _OPERATION_CONTEXT_KEY: _OPERATION_TOKEN,
        }).sudo().write({
            'state': 'done',
            'completed_at': fields.Datetime.now(),
            'result_json': result,
        })
        return self.browse(self.ids)

    @api.constrains('idempotency_key', 'payload_hash')
    def _check_canonical_metadata(self):
        for operation in self:
            if not (operation.idempotency_key or '').strip():
                raise ValidationError('The idempotency key is required.')
            if len(operation.payload_hash or '') != 64:
                raise ValidationError('The canonical payload hash must be SHA-256.')

    def unlink(self):
        raise ValidationError('Consignment operation journals cannot be deleted.')
