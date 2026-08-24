import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round


class LoyaltyConsignCardToken(models.Model):
    """Durable serialization tuple for automatic consignment card creation."""

    _name = 'loyalty.consign.card.token'
    _description = 'Consignment Card Creation Token'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade', index=True)
    program_id = fields.Many2one(
        'loyalty.program', required=True, ondelete='cascade', index=True,
        check_company=True,
    )
    partner_id = fields.Many2one(
        'res.partner', required=True, ondelete='cascade', index=True,
        check_company=True,
    )

    _sql_constraints = [
        (
            'company_program_partner_unique',
            'unique(company_id, program_id, partner_id)',
            'Only one card creation token is allowed per company, program, and partner.',
        ),
    ]


class LoyaltyConsignEngine(models.AbstractModel):
    """Deep private server interface for authoritative consignment commands."""

    _name = 'loyalty.consign.engine'
    _description = 'Private Consignment Entitlement Engine'

    @api.model
    def _source_snapshot(self, source):
        if not isinstance(source, models.BaseModel):
            raise ValidationError('The operation source must be a trusted record.')
        source.ensure_one()
        source.check_access('read')
        if not source.exists() or not source.id:
            raise ValidationError('The operation source no longer exists.')
        return {
            'record': source,
            'model': source._name,
            'res_id': source.id,
            'name': (source.display_name or f'{source._name},{source.id}').strip(),
        }

    @api.model
    def _coerce_record(self, model_name, value, label):
        record = value if isinstance(value, models.BaseModel) else self.env[model_name].browse(value)
        record.ensure_one()
        if record._name != model_name or not record.exists():
            raise ValidationError(f'The {label} is required and must exist.')
        return record

    @api.model
    def _command_company(self, source, partner, program):
        source_company = source.company_id if 'company_id' in source._fields else False
        if not source_company:
            raise ValidationError('The issue source must belong to an explicit company.')
        if not program.company_id or program.company_id != source_company:
            raise ValidationError(
                'The consignment program must belong to the exact source company.'
            )
        if partner.company_id and partner.company_id != source_company:
            raise ValidationError('The customer belongs to another company.')
        if (
            (source._name == 'res.partner' and source != partner)
            or ('partner_id' in source._fields and source.partner_id != partner)
        ):
            raise ValidationError('The source customer does not match the issue customer.')
        if 'program_id' in source._fields and source.program_id != program:
            raise ValidationError('The source program does not match the issue program.')
        return source_company

    @api.model
    def _normalize_grants(self, grants, default_source, company, partner):
        if not isinstance(grants, (list, tuple)) or not grants:
            raise ValidationError('At least one positive consignment grant is required.')
        normalized = []
        for grant in grants:
            if not isinstance(grant, dict):
                raise ValidationError('Every consignment grant must be a normalized mapping.')
            product = self._coerce_record(
                'product.product', grant.get('product') or grant.get('product_id'), 'grant product',
            )
            grant_uom = self._coerce_record(
                'uom.uom',
                grant.get('product_uom') or grant.get('product_uom_id')
                or grant.get('uom') or product.uom_id,
                'grant UoM',
            )
            if product.uom_id.category_id != grant_uom.category_id:
                raise ValidationError('The grant product and UoM must have the same category.')
            if product.company_id and product.company_id != company:
                raise ValidationError('The grant product belongs to another company.')
            explicit_source = grant.get('source')
            source_line = grant.get('source_line')
            if explicit_source and source_line and explicit_source != source_line:
                raise ValidationError('Grant source and source_line must identify the same record.')
            source_snapshot = self._source_snapshot(
                source_line or explicit_source or default_source
            )
            grant_source = source_snapshot['record']
            grant_company = (
                grant_source.company_id
                if 'company_id' in grant_source._fields else False
            )
            if not grant_company or grant_company != company:
                raise ValidationError(
                    'Every grant source must belong to the exact operation company.'
                )
            grant_partner = False
            if grant_source._name == 'res.partner':
                grant_partner = grant_source
            elif 'partner_id' in grant_source._fields:
                grant_partner = grant_source.partner_id
            elif 'order_id' in grant_source._fields and (
                'partner_id' in grant_source.order_id._fields
            ):
                grant_partner = grant_source.order_id.partner_id
            if grant_partner and grant_partner != partner:
                raise ValidationError(
                    'Every grant source must belong to the exact issue customer.'
                )
            quantity = grant_uom._compute_quantity(
                grant.get('quantity', 0.0), product.uom_id, round=False,
            )
            quantity = float_round(quantity, precision_rounding=product.uom_id.rounding)
            if float_compare(
                quantity, 0.0, precision_rounding=product.uom_id.rounding,
            ) <= 0:
                raise ValidationError('Grant quantity must be positive after UoM rounding.')
            # Caller-provided prices are never audit authority. Task 4 uses the
            # trusted current product list price as the server-side snapshot.
            unit_value = company.currency_id.round(product.list_price or 0.0)
            identity = {
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'quantity': quantity,
                'source_channel': grant.get('source_channel', 'manual'),
                'source_model': source_snapshot['model'],
                'source_res_id': source_snapshot['res_id'],
                'provenance_key': str(grant.get('provenance_key') or ''),
            }
            normalized.append({
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'quantity': quantity,
                'unit_value': unit_value,
                'source_channel': grant.get('source_channel', 'manual'),
                'source_model': source_snapshot['model'],
                'source_res_id': source_snapshot['res_id'],
                'source_name': source_snapshot['name'],
                'provenance_key': identity['provenance_key'],
                'product_desc_snapshot': product.display_name.strip(),
                'lot_snapshot': grant.get('lot_snapshot') or False,
                'storage_snapshot': grant.get('storage_snapshot') or False,
                'identity': identity,
            })
        allowed_channels = dict(self.env['loyalty.consign.movement']._fields[
            'source_channel'
        ].selection)
        if any(grant['source_channel'] not in allowed_channels for grant in normalized):
            raise ValidationError('The grant source channel is invalid.')
        # Mutable names, prices, descriptions, and storage snapshots are audit
        # data only. Input order and those snapshots are not command identity.
        return sorted(normalized, key=lambda item: json.dumps(
            item['identity'], sort_keys=True, separators=(',', ':'),
            ensure_ascii=False,
        ))

    @api.model
    def _lock_card_tuple(self, company, program, partner):
        digest = hashlib.sha256(
            f'{company.id}:{program.id}:{partner.id}'.encode()
        ).digest()
        token = int.from_bytes(digest[:8], 'big', signed=True)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', (token,))
        # The insert/update is a durable stale-snapshot fence. A waiter whose
        # REPEATABLE READ snapshot predates the winning command receives a
        # SerializationFailure here; Odoo retries the whole request.
        self.env.cr.execute(
            '''
                INSERT INTO loyalty_consign_card_token
                            (company_id, program_id, partner_id, create_uid, write_uid,
                             create_date, write_date)
                     VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (company_id, program_id, partner_id)
                DO UPDATE SET write_date = loyalty_consign_card_token.write_date
                RETURNING id
            ''',
            (company.id, program.id, partner.id, self.env.uid, self.env.uid),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('The card creation serialization token is unavailable.')

    @api.model
    def _find_or_create_card(self, company, program, partner):
        self._lock_card_tuple(company, program, partner)
        self.env.cr.execute(
            '''
                SELECT id
                  FROM loyalty_card
                 WHERE program_id = %s
                   AND partner_id = %s
                   AND active = TRUE
              ORDER BY id
                   FOR UPDATE
            ''',
            (program.id, partner.id),
        )
        cards = self.env['loyalty.card'].sudo().browse(
            [row[0] for row in self.env.cr.fetchall()]
        ).filtered(lambda card: (
            card.is_consign and card.company_id == company
            and card.program_id.company_id == company
        ))
        if len(cards) > 1:
            raise ValidationError(
                'Multiple active consignment cards already exist for this customer and program.'
            )
        if cards:
            return cards, False
        card = self.env['loyalty.card'].with_context(
            loyalty_no_mail=True,
        ).sudo().create({
            'program_id': program.id,
            'partner_id': partner.id,
            'points': 0,
        })
        return self.env['loyalty.card'].browse(card.ids), True

    @api.model
    def _issue(self, source, partner, program, grants, idempotency_key):
        """Validate and idempotently issue one command with many grant facts."""
        source_snapshot = self._source_snapshot(source)
        partner = self._coerce_record('res.partner', partner, 'customer')
        program = self._coerce_record('loyalty.program', program, 'consignment program')
        if program.program_type != 'consign' or not program.active:
            raise ValidationError('Issues require an active consignment program.')
        company = self._command_company(source_snapshot['record'], partner, program)
        normalized_grants = self._normalize_grants(
            grants, source_snapshot['record'], company, partner,
        )
        payload = {
            'program_id': program.id,
            'company_id': company.id,
            'grants': [
                {key: value for key, value in grant.items() if key != 'identity'}
                for grant in normalized_grants
            ],
        }
        identity_payload = {
            'program_id': program.id,
            'company_id': company.id,
            'grants': [grant['identity'] for grant in normalized_grants],
        }

        # All source, dimension, UoM, company, and quantity validation above is
        # deliberately pure and precedes durable journal insertion.
        operation, replay = self.env['loyalty.consign.operation']._open_command(
            operation_type='issue',
            company=company,
            partner=partner,
            source_model=source_snapshot['model'],
            source_res_id=source_snapshot['res_id'],
            source_name=source_snapshot['name'],
            idempotency_key=idempotency_key,
            payload=payload,
            identity_payload=identity_payload,
        )
        if replay:
            if operation.state != 'done':
                raise ValidationError('The replayed issue command is not complete.')
            return operation

        card, card_created = self._find_or_create_card(company, program, partner)
        projection_model = self.env['loyalty.consign.line']
        movement_model = self.env['loyalty.consign.movement']
        projections = projection_model.browse()
        movements = movement_model.browse()
        occurrence = {}
        for grant in normalized_grants:
            projection = projection_model._get_or_create_projection(
                card=card,
                product=self.env['product.product'].browse(grant['product_id']),
                product_uom=self.env['uom.uom'].browse(grant['product_uom_id']),
                metadata=grant,
            )
            projections |= projection
            canonical = json.dumps(
                grant['identity'], sort_keys=True, separators=(',', ':'),
                ensure_ascii=False,
            )
            digest = hashlib.sha256(canonical.encode()).hexdigest()[:20]
            occurrence[digest] = occurrence.get(digest, 0) + 1
            movements |= movement_model._append_to_operation(
                operation=operation,
                aggregate_line=projection,
                movement_type='issue',
                quantity=grant['quantity'],
                source_channel=grant['source_channel'],
                source_model=grant['source_model'],
                source_res_id=grant['source_res_id'],
                source_name=grant['source_name'],
                idempotency_key=(
                    f'{idempotency_key}:grant:{digest}:{occurrence[digest]}'
                ),
                unit_value=grant['unit_value'],
                product_desc_snapshot=grant['product_desc_snapshot'],
                lot_snapshot=grant['lot_snapshot'],
                storage_snapshot=grant['storage_snapshot'],
                reconcile=False,
            )
        projections._reconcile_projection()
        operation._complete_from_engine({
            'card_id': card.id,
            'projection_ids': sorted(projections.ids),
            'movement_ids': sorted(movements.ids),
        })
        if card_created:
            # Mail is emitted only on the first completed command. A replay
            # returns above and cannot re-notify.
            self.env['loyalty.card'].browse(card.ids)._send_creation_communication(
                force_send=False,
            )
        return operation
