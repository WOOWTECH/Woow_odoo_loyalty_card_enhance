from datetime import timedelta
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

    @api.model
    def _authorization_company(self, source, partner):
        company = source.company_id if 'company_id' in source._fields else False
        if not company:
            raise ValidationError(
                'The authorization source must belong to an explicit company.'
            )
        if partner.company_id and partner.company_id != company:
            raise ValidationError('The customer belongs to another company.')
        source_partner = False
        if source._name == 'res.partner':
            source_partner = source
        elif 'partner_id' in source._fields:
            source_partner = source.partner_id
        elif 'order_id' in source._fields and 'partner_id' in source.order_id._fields:
            source_partner = source.order_id.partner_id
        if source_partner and source_partner != partner:
            raise ValidationError(
                'The authorization source customer does not match the exact customer.'
            )
        return company

    @api.model
    def _normalize_authorization_requests(self, requests, company, partner):
        """Canonicalize request facts without consulting mutable card state."""
        if not isinstance(requests, (list, tuple)) or not requests:
            raise ValidationError('At least one positive authorization request is required.')
        forbidden = {
            'price', 'price_unit', 'unit_price', 'unit_value', 'amount',
            'value', 'value_delta', 'issue', 'issue_id', 'issue_movement',
            'issue_movement_id', 'movement', 'movement_id',
            'original_movement', 'original_movement_id',
        }
        aggregated = {}
        for request in requests:
            if not isinstance(request, dict):
                raise ValidationError(
                    'Every authorization request must be a normalized mapping.'
                )
            if forbidden & set(request):
                raise ValidationError(
                    'Authorization requests cannot provide price or issue movement data.'
                )
            card = self._coerce_record(
                'loyalty.card',
                request.get('card') or request.get('card_id')
                or request.get('loyalty_card') or request.get('loyalty_card_id'),
                'consignment card',
            )
            product = self._coerce_record(
                'product.product',
                request.get('product') or request.get('product_id'),
                'authorization product variant',
            )
            request_uom = self._coerce_record(
                'uom.uom',
                request.get('product_uom') or request.get('product_uom_id')
                or request.get('uom') or request.get('uom_id') or product.uom_id,
                'authorization UoM',
            )
            card.check_access('read')
            product.check_access('read')
            request_uom.check_access('read')
            if product.uom_id.category_id != request_uom.category_id:
                raise ValidationError(
                    'The authorization product and UoM must have the same category.'
                )
            raw_quantity = request.get(
                'quantity', request.get('qty', request.get('requested_quantity', 0.0)),
            )
            quantity = request_uom._compute_quantity(
                raw_quantity, product.uom_id, round=False,
            )
            if quantity <= 0.0:
                raise ValidationError(
                    'Every authorization quantity must be positive before aggregation.'
                )
            key = (card.id, product.id, product.uom_id.id)
            if key not in aggregated:
                aggregated[key] = {
                    'card_id': card.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'request_uom_ids': set(),
                    'quantity': 0.0,
                }
            aggregated[key]['request_uom_ids'].add(request_uom.id)
            aggregated[key]['quantity'] += quantity
        normalized = []
        for key in sorted(aggregated):
            item = aggregated[key]
            product = self.env['product.product'].browse(item['product_id'])
            item['quantity'] = float_round(
                item['quantity'], precision_rounding=product.uom_id.rounding,
            )
            if float_compare(
                item['quantity'], 0.0,
                precision_rounding=product.uom_id.rounding,
            ) <= 0:
                raise ValidationError(
                    'Authorization quantity must be positive after UoM rounding.'
                )
            item['request_uom_ids'] = sorted(item['request_uom_ids'])
            normalized.append(item)
        return normalized

    @api.model
    def _validate_authorization_dimensions(self, normalized, company, partner):
        """Validate mutable command dimensions before opening a new journal row."""
        for item in normalized:
            card = self.env['loyalty.card'].sudo().browse(item['card_id']).exists()
            product = self.env['product.product'].sudo().browse(item['product_id']).exists()
            if not card or not product:
                raise ValidationError('The authorization card or product no longer exists.')
            if not card.active or not card.is_consign:
                raise ValidationError('Authorization requires an active consignment card.')
            if not card.partner_id or card.partner_id != partner:
                raise ValidationError(
                    'The consignment card owner must match the exact customer.'
                )
            if (
                not card.company_id or not card.program_id.company_id
                or card.company_id != company or card.program_id.company_id != company
            ):
                raise ValidationError(
                    'The card and program must belong to the exact authorization company.'
                )
            if not card.program_id.active or card.program_id.program_type != 'consign':
                raise ValidationError(
                    'Authorization requires an active consignment program.'
                )
            if product.company_id and product.company_id != company:
                raise ValidationError('The authorization product belongs to another company.')
            request_uoms = self.env['uom.uom'].sudo().browse(item['request_uom_ids'])
            if len(request_uoms) != len(item['request_uom_ids']) or any(
                request_uom.category_id != product.uom_id.category_id
                for request_uom in request_uoms
            ):
                raise ValidationError(
                    'The authorization product and UoM must have the same category.'
                )
            projection = self.env['loyalty.consign.line'].sudo().search([
                ('card_id', '=', card.id),
                ('product_id', '=', product.id),
                ('product_uom_id', '=', product.uom_id.id),
            ], limit=1)
            if not projection:
                raise ValidationError(
                    'The exact card, product variant, and base UoM projection is missing.'
                )
            item['projection_id'] = projection.id

    @api.model
    def _find_completed_authorization_replay(
        self, company, partner, source_snapshot, idempotency_key, identity_requests,
    ):
        """Return an exact completed replay before mutable state validation."""
        operation_model = self.env['loyalty.consign.operation']
        _canonical, payload_hash = operation_model._canonical_payload({
            'operation_type': 'authorize',
            'partner_id': partner.id,
            'source_model': source_snapshot['model'],
            'source_res_id': source_snapshot['res_id'],
            'payload': {'requests': identity_requests},
        })
        operation = operation_model.sudo().search([
            ('company_id', '=', company.id),
            ('idempotency_key', '=', idempotency_key),
        ], limit=1)
        if not operation:
            return False
        if operation.payload_hash != payload_hash:
            raise ValidationError(
                'The idempotency key was already used with a different payload.'
            )
        if operation.state == 'done' and len(operation.hold_ids) == 1:
            return operation_model.browse(operation.ids)
        return False

    @api.model
    def _lock_authorization_dimensions(self, normalized, company, partner):
        card_ids = sorted({item['card_id'] for item in normalized})
        projection_ids = sorted({item['projection_id'] for item in normalized})
        for card_id in card_ids:
            self.env.cr.execute(
                '''UPDATE loyalty_card SET write_date = write_date
                    WHERE id = %s RETURNING id''',
                (card_id,),
            )
            if not self.env.cr.fetchone():
                raise ValidationError('The authorization card no longer exists.')
        for projection_id in projection_ids:
            self.env.cr.execute(
                '''UPDATE loyalty_consign_line SET write_date = write_date
                    WHERE id = %s RETURNING id''',
                (projection_id,),
            )
            if not self.env.cr.fetchone():
                raise ValidationError('The authorization projection no longer exists.')
        cards = self.env['loyalty.card'].sudo().browse(card_ids)
        projections = self.env['loyalty.consign.line'].sudo().browse(projection_ids)
        cards.invalidate_recordset()
        projections.invalidate_recordset()
        for item in normalized:
            card = cards.browse(item['card_id'])
            line = projections.browse(item['projection_id'])
            if (
                not card.active or not card.is_consign
                or not card.partner_id or card.partner_id != partner
                or not card.company_id or not card.program_id.company_id
                or card.company_id != company or card.program_id.company_id != company
                or not card.program_id.active or card.program_id.program_type != 'consign'
                or line.card_id != card or line.partner_id != card.partner_id
                or line.product_id.id != item['product_id']
                or line.product_uom_id.id != item['product_uom_id']
            ):
                raise ValidationError(
                    'An authorization dimension changed while the command was locking.'
                )
        projections._reconcile_projection()

        # Existing Holds precede issue rows in the global hierarchy. A new Hold
        # is not inserted until the complete capacity plan has passed.
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_hold
                WHERE state = 'active'
                  AND id IN (
                      SELECT hold_id FROM loyalty_consign_hold_allocation
                       WHERE aggregate_line_id = ANY(%s)
                  )
             ORDER BY id
                  FOR UPDATE''',
            (projection_ids,),
        )
        self.env.cr.fetchall()
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_movement
                WHERE aggregate_line_id = ANY(%s)
             ORDER BY id
                  FOR UPDATE''',
            (projection_ids,),
        )
        self.env.cr.fetchall()
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_hold_allocation
                WHERE aggregate_line_id = ANY(%s)
             ORDER BY id
                  FOR UPDATE''',
            (projection_ids,),
        )
        self.env.cr.fetchall()
        projections.invalidate_recordset()
        return cards, projections

    @api.model
    def _authorization_allocation_plan(self, normalized, projections):
        movement_model = self.env['loyalty.consign.movement']
        plan = []
        for item in normalized:
            line = projections.browse(item['projection_id'])
            rounding = line.product_uom_id.rounding
            remaining = item['quantity']
            if float_compare(
                remaining, line.qty_available, precision_rounding=rounding,
            ) > 0:
                raise ValidationError(
                    'The authorization request exceeds authoritative available quantity.'
                )
            states = movement_model._fifo_issue_availability(
                line, include_active_holds=True,
            )
            for state in states:
                allocated = min(remaining, state['available'])
                allocated = float_round(allocated, precision_rounding=rounding)
                if float_compare(
                    allocated, 0.0, precision_rounding=rounding,
                ) <= 0:
                    continue
                plan.append({
                    'aggregate_line_id': line.id,
                    'issue_movement_id': state['issue'].id,
                    'quantity': allocated,
                })
                remaining = float_round(
                    remaining - allocated, precision_rounding=rounding,
                )
                if float_compare(
                    remaining, 0.0, precision_rounding=rounding,
                ) <= 0:
                    break
            if float_compare(
                remaining, 0.0, precision_rounding=rounding,
            ) > 0:
                raise ValidationError(
                    'The authorization request cannot be allocated to exact FIFO issues.'
                )
        return plan

    @api.model
    def _authorize(self, source, partner, requests, idempotency_key):
        """Atomically authorize exact card/product quantities for 30 minutes."""
        source_snapshot = self._source_snapshot(source)
        partner = self._coerce_record('res.partner', partner, 'customer')
        company = self._authorization_company(source_snapshot['record'], partner)
        normalized = self._normalize_authorization_requests(
            requests, company, partner,
        )
        identity_requests = [{
            'card_id': item['card_id'],
            'product_id': item['product_id'],
            'product_uom_id': item['product_uom_id'],
            'quantity': item['quantity'],
        } for item in normalized]
        replay = self._find_completed_authorization_replay(
            company, partner, source_snapshot, idempotency_key, identity_requests,
        )
        if replay:
            return replay

        # Canonicalization above is intentionally independent of later mutable
        # card/program state so exact completed replays remain durable. Every
        # new command validates all mutable dimensions before opening its journal.
        self._validate_authorization_dimensions(normalized, company, partner)
        operation, replay = self.env['loyalty.consign.operation']._open_command(
            operation_type='authorize',
            company=company,
            partner=partner,
            source_model=source_snapshot['model'],
            source_res_id=source_snapshot['res_id'],
            source_name=source_snapshot['name'],
            idempotency_key=idempotency_key,
            payload={'requests': identity_requests},
            identity_payload={'requests': identity_requests},
        )
        if replay:
            if operation.state != 'done' or len(operation.hold_ids) != 1:
                raise ValidationError('The replayed authorization command is not complete.')
            return operation

        cards, projections = self._lock_authorization_dimensions(
            normalized, company, partner,
        )
        plan = self._authorization_allocation_plan(normalized, projections)
        now = fields.Datetime.now()
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': operation.id,
            'company_id': company.id,
            'partner_id': partner.id,
            'state': 'active',
            'expires_at': now + timedelta(minutes=30),
            'source_model': source_snapshot['model'],
            'source_res_id': source_snapshot['res_id'],
            'source_name': source_snapshot['name'],
        })
        allocations = self.env[
            'loyalty.consign.hold.allocation'
        ]._create_planned_from_engine(hold, plan)
        projections._reconcile_projection()
        operation._complete_from_engine({
            'hold_id': hold.id,
            'allocation_ids': sorted(allocations.ids),
            'projection_ids': sorted(projections.ids),
            'card_ids': sorted(cards.ids),
        })
        return operation
