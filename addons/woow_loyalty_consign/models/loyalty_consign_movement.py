from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.tools import float_compare, float_round

from ..hooks import ensure_movement_immutability_trigger


_APPEND_CONTEXT_KEY = '_woow_consign_append_movement_token'
_APPEND_TOKEN = object()


class LoyaltyConsignMovement(models.Model):
    """Immutable consignment fact.

    Task 4 makes these facts authoritative and deterministically reconciles
    the aggregate line projection after every posted change.
    """

    _name = 'loyalty.consign.movement'
    _description = 'Immutable Consignment Movement'
    _order = 'occurred_at desc, id desc'
    _check_company_auto = True

    operation_id = fields.Many2one(
        'loyalty.consign.operation', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    aggregate_line_id = fields.Many2one(
        'loyalty.consign.line', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    card_id = fields.Many2one(
        'loyalty.card', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    partner_id = fields.Many2one(
        'res.partner', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True, ondelete='restrict',
    )
    product_id = fields.Many2one(
        'product.product', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', required=True, index=True, ondelete='restrict',
    )
    quantity = fields.Float(required=True)
    currency_id = fields.Many2one(
        'res.currency', required=True, index=True, ondelete='restrict',
    )
    unit_value = fields.Monetary(
        required=True, currency_field='currency_id',
    )
    value_delta = fields.Monetary(
        required=True, currency_field='currency_id',
    )
    movement_type = fields.Selection(
        [
            ('issue', 'Issue'),
            ('redeem', 'Redeem'),
            ('redeem_reversal', 'Redeem Reversal'),
            ('issue_reversal', 'Issue Reversal'),
            ('adjustment_in', 'Adjustment In'),
            ('adjustment_out', 'Adjustment Out'),
        ], required=True, index=True,
    )
    occurred_at = fields.Datetime(required=True, index=True)
    source_channel = fields.Selection(
        [('migration', 'Migration'), ('sale', 'Sales'), ('manual', 'Manual')],
        required=True, index=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_res_id = fields.Integer(required=True, index=True)
    source_name = fields.Char(required=True)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    original_movement_id = fields.Many2one(
        'loyalty.consign.movement', index=True, ondelete='restrict',
        check_company=True,
    )
    hold_allocation_ids = fields.One2many(
        'loyalty.consign.hold.allocation', 'issue_movement_id', copy=False,
    )
    product_desc_snapshot = fields.Char(required=True)
    lot_snapshot = fields.Char()
    storage_snapshot = fields.Char()

    _sql_constraints = [
        (
            'quantity_positive',
            'CHECK(quantity > 0)',
            'Movement quantity must be positive.',
        ),
        (
            'source_res_id_positive',
            'CHECK(source_res_id > 0)',
            'The movement source record must be valid.',
        ),
    ]

    def init(self):
        ensure_movement_immutability_trigger(self.env.cr)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(_APPEND_CONTEXT_KEY) is not _APPEND_TOKEN:
            raise ValidationError(
                'Movements can only be appended through the private ledger interface.'
            )
        return super().create(vals_list)

    def write(self, vals):
        raise ValidationError('Posted consignment movements are immutable.')

    def unlink(self):
        raise ValidationError('Posted consignment movements cannot be deleted.')

    @api.model
    def _operation_type_for_movement(self, movement_type):
        if movement_type == 'issue':
            return 'issue'
        if movement_type == 'redeem':
            return 'capture'
        if movement_type in ('redeem_reversal', 'issue_reversal'):
            return 'reverse'
        return 'adjust'

    @api.model
    def _lock_original_operation_token(self, original_movement):
        """Serialize linked-cap decisions on the original operation tuple."""
        operation_id = original_movement.operation_id.id
        if not operation_id:
            raise ValidationError('The original movement operation is required.')
        self.env.cr.execute(
            '''
                UPDATE loyalty_consign_operation
                   SET write_date = write_date
                 WHERE id = %s
             RETURNING id
            ''',
            (operation_id,),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('The original movement operation no longer exists.')

    @api.model
    def _lock_projection_token(self, aggregate_line):
        """Durably serialize all outgoing balance decisions per projection."""
        self.env.cr.execute(
            '''UPDATE loyalty_consign_line
                  SET write_date = write_date
                WHERE id = %s
            RETURNING id''',
            (aggregate_line.id,),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('The consignment projection no longer exists.')
        aggregate_line.invalidate_recordset([
            'qty_remaining', 'qty_available', 'amount_remaining', 'movement_ids',
        ])

    @api.model
    def _fifo_issue_availability(self, aggregate_line, include_active_holds=True):
        """Return exact FIFO issue capacity after posted consumption."""
        aggregate_line.ensure_one()
        movements = aggregate_line.sudo().movement_ids
        issues = movements.filtered(
            lambda movement: movement.movement_type == 'issue'
        ).sorted(lambda movement: (movement.occurred_at, movement.id))
        rounding = aggregate_line.product_uom_id.rounding
        states = []
        for issue in issues:
            revoked = sum(movements.filtered(
                lambda movement: movement.movement_type == 'issue_reversal'
                and movement.original_movement_id == issue
            ).mapped('quantity'))
            linked_redeems = movements.filtered(
                lambda movement: movement.movement_type == 'redeem'
                and movement.original_movement_id == issue
            )
            restored = sum(movements.filtered(
                lambda movement: movement.movement_type == 'redeem_reversal'
                and movement.original_movement_id in linked_redeems
            ).mapped('quantity'))
            remaining = (
                issue.quantity - revoked
                - sum(linked_redeems.mapped('quantity')) + restored
            )
            if float_compare(
                remaining, 0.0, precision_rounding=rounding,
            ) < 0:
                raise ValidationError(
                    'Linked issue consumption exceeds the unreversed issue quantity.'
                )
            states.append({'issue': issue, 'available': max(0.0, remaining)})

        # Pre-Task 4 redemption facts did not identify an issue. Attribute
        # each net legacy fact to FIFO issues with the same value snapshot.
        # Guessing by quantity alone can make a later cancellation reverse a
        # different-value issue and produce a negative ledger value. Ambiguous
        # historical facts are therefore blocked for controlled repair instead
        # of silently corrupting the projection.
        unlinked_redeems = movements.filtered(
            lambda movement: movement.movement_type == 'redeem'
            and not movement.original_movement_id
        ).sorted(lambda movement: (movement.occurred_at, movement.id))
        currency_rounding = aggregate_line.currency_id.rounding
        for redeem in unlinked_redeems:
            restored = sum(movements.filtered(
                lambda movement: movement.movement_type == 'redeem_reversal'
                and movement.original_movement_id == redeem
            ).mapped('quantity'))
            unlinked_remaining = redeem.quantity - restored
            if float_compare(
                unlinked_remaining, 0.0, precision_rounding=rounding,
            ) < 0:
                raise ValidationError(
                    'Legacy redemption reversal exceeds its original quantity.'
                )
            matching_states = [
                state for state in states
                if float_compare(
                    state['issue'].unit_value, redeem.unit_value,
                    precision_rounding=currency_rounding,
                ) == 0
            ]
            for state in matching_states:
                consumed = min(unlinked_remaining, state['available'])
                state['available'] -= consumed
                unlinked_remaining -= consumed
                if float_compare(
                    unlinked_remaining, 0.0, precision_rounding=rounding,
                ) <= 0:
                    break
            if float_compare(
                unlinked_remaining, 0.0, precision_rounding=rounding,
            ) > 0:
                raise ValidationError(
                    'Legacy redemption value cannot be reconciled to exact FIFO issues.'
                )

        # Adjustments do not identify an issue row. Attribute their net
        # outgoing quantity to FIFO issues, while adjustment-in first restores
        # prior adjustment-out capacity. An unmatched positive adjustment-in
        # remains in the aggregate projection but cannot fabricate an issue row
        # for an exact Hold allocation.
        adjusted_out = sum(movements.filtered(
            lambda movement: movement.movement_type == 'adjustment_out'
        ).mapped('quantity'))
        adjusted_in = sum(movements.filtered(
            lambda movement: movement.movement_type == 'adjustment_in'
        ).mapped('quantity'))
        adjustment_consumption = max(0.0, adjusted_out - adjusted_in)
        for state in states:
            consumed = min(adjustment_consumption, state['available'])
            state['available'] -= consumed
            adjustment_consumption -= consumed
            if float_compare(
                adjustment_consumption, 0.0, precision_rounding=rounding,
            ) <= 0:
                break
        if float_compare(
            adjustment_consumption, 0.0, precision_rounding=rounding,
        ) > 0:
            raise ValidationError(
                'Adjustment consumption exceeds exact FIFO issue capacity.'
            )

        if include_active_holds:
            for state in states:
                held = sum(state['issue'].sudo().hold_allocation_ids.filtered(
                    lambda allocation: allocation.hold_id.state == 'active'
                ).mapped('quantity'))
                state['available'] = max(0.0, state['available'] - held)
        return states

    @api.model
    def _append_movement(
        self, *, aggregate_line, movement_type, quantity, source_channel,
        source_model, source_res_id, source_name, idempotency_key,
        occurred_at=None, unit_value=None, original_movement=None,
        product_desc_snapshot=None, lot_snapshot=None, storage_snapshot=None,
        allow_inactive_card=False, operation=None, reconcile=True,
    ):
        """Append a fact, optionally to a caller-owned pending operation."""
        aggregate_line = (
            aggregate_line if hasattr(aggregate_line, 'id')
            else self.env['loyalty.consign.line'].browse(aggregate_line)
        )
        aggregate_line.ensure_one()
        card = aggregate_line.card_id
        company = card.company_id
        if not company or card.program_id.company_id != company:
            raise ValidationError(
                'Movements require a card and program in one explicit company.'
            )
        partner = card.partner_id
        product = aggregate_line.product_id
        source_name = (
            (source_name or '').strip()
            or (aggregate_line.product_desc or '').strip()
            or (product.display_name or '').strip()
            or f'{source_model},{source_res_id}'
        )
        uom = aggregate_line.product_uom_id
        currency = aggregate_line.currency_id or company.currency_id
        original_movement = original_movement or self.browse()
        if original_movement:
            original_movement.ensure_one()

        rounded_quantity = float_round(
            quantity, precision_rounding=uom.rounding,
        )
        if float_compare(
            rounded_quantity, 0.0, precision_rounding=uom.rounding,
        ) <= 0:
            raise ValidationError('Movement quantity must be positive after UoM rounding.')
        if not card.is_consign or (not card.active and not allow_inactive_card):
            raise ValidationError('Movements require an active consignment card.')
        if aggregate_line.partner_id != partner or not partner:
            raise ValidationError('The aggregate line partner must match its card.')
        if aggregate_line.program_id != card.program_id:
            raise ValidationError('The aggregate line program must match its card.')
        if product.uom_id.category_id != uom.category_id:
            raise ValidationError('The product and movement UoM categories must match.')
        if product.company_id and product.company_id != company:
            raise ValidationError('The movement product belongs to another company.')
        if aggregate_line.company_id and aggregate_line.company_id != company:
            raise ValidationError('The aggregate line belongs to another company.')

        original_targets = {
            'redeem': 'issue',
            'redeem_reversal': 'redeem',
            'issue_reversal': 'issue',
        }
        expected_original_type = original_targets.get(movement_type)
        original_required = movement_type in ('redeem_reversal', 'issue_reversal')
        if original_required and not original_movement:
            raise ValidationError('A reversal must reference its original movement.')
        if original_movement:
            if original_movement.movement_type != expected_original_type:
                raise ValidationError('The original movement has an incompatible type.')
            dimensions = (
                ('company_id', company), ('partner_id', partner),
                ('card_id', card), ('aggregate_line_id', aggregate_line),
                ('product_id', product), ('product_uom_id', uom),
                ('currency_id', currency),
            )
            if any(getattr(original_movement, field) != value for field, value in dimensions):
                raise ValidationError('Linked movements must preserve all original dimensions.')

        if original_movement:
            if unit_value is not None and float_compare(
                unit_value, original_movement.unit_value,
                precision_rounding=currency.rounding,
            ) != 0:
                raise ValidationError(
                    'A linked movement must preserve its original unit value.'
                )
            unit_value = original_movement.unit_value
        elif unit_value is None:
            unit_value = aggregate_line.unit_price
        value_delta = currency.round(rounded_quantity * unit_value)
        payload = {
            'aggregate_line_id': aggregate_line.id,
            'card_id': card.id,
            'product_id': product.id,
            'product_uom_id': uom.id,
            'quantity': rounded_quantity,
            'currency_id': currency.id,
            'unit_value': unit_value,
            'value_delta': value_delta,
            'movement_type': movement_type,
            'source_channel': source_channel,
            'original_movement_id': original_movement.id or False,
            'product_desc_snapshot': (
                product_desc_snapshot or aggregate_line.product_desc
                or product.display_name
            ),
            'lot_snapshot': (
                lot_snapshot if lot_snapshot is not None
                else aggregate_line.lot_id.name
            ),
            'storage_snapshot': (
                storage_snapshot if storage_snapshot is not None
                else aggregate_line.storage_note
            ),
        }
        owns_operation = not operation
        if owns_operation:
            operation, replay = self.env['loyalty.consign.operation']._open_command(
                operation_type=self._operation_type_for_movement(movement_type),
                company=company,
                partner=partner,
                source_model=source_model,
                source_res_id=source_res_id,
                source_name=source_name,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            existing = self.sudo().search([
                ('operation_id', '=', operation.id),
            ], limit=1)
            if replay and existing:
                return self.browse(existing.ids)
            if replay and operation.state == 'done':
                raise ValidationError('The replayed command has no movement result.')
        else:
            operation = operation.sudo()
            operation.ensure_one()
            if (
                operation.state != 'pending'
                or operation.company_id != company
                or operation.partner_id != partner
                or operation.operation_type != self._operation_type_for_movement(movement_type)
            ):
                raise ValidationError(
                    'The movement dimensions do not match the pending operation.'
                )

        outgoing_types = {'redeem', 'issue_reversal', 'adjustment_out'}
        if original_movement:
            # Lock order is command advisory/per-key token, original operation
            # token, then aggregate projection token. Hold allocation follows
            # the same order. Durable no-op updates fence stale snapshots.
            self._lock_original_operation_token(original_movement)
        if movement_type in outgoing_types:
            self._lock_projection_token(aggregate_line)
            if float_compare(
                rounded_quantity, aggregate_line.qty_available,
                precision_rounding=uom.rounding,
            ) > 0 or float_compare(
                value_delta, aggregate_line.amount_remaining,
                precision_rounding=currency.rounding,
            ) > 0:
                raise ValidationError(
                    'An outgoing movement cannot exceed projection availability.'
                )

        if original_movement:
            if (
                movement_type == 'issue_reversal'
                and original_movement.sudo().hold_allocation_ids.filtered(
                    lambda allocation: allocation.hold_id.state == 'active'
                )
            ):
                raise ValidationError(
                    'An issue with an active Consignment Hold allocation cannot be reversed.'
                )
            if movement_type in ('redeem', 'issue_reversal'):
                states = self._fifo_issue_availability(
                    aggregate_line,
                    include_active_holds=movement_type == 'redeem',
                )
                capacity = next(
                    (state['available'] for state in states
                     if state['issue'] == original_movement),
                    0.0,
                )
                if float_compare(
                    rounded_quantity, capacity,
                    precision_rounding=uom.rounding,
                ) > 0:
                    raise ValidationError(
                        'A linked outgoing movement exceeds unused issue capacity.'
                    )
            else:
                linked_quantity = sum(self.sudo().search([
                    ('original_movement_id', '=', original_movement.id),
                    ('movement_type', '=', movement_type),
                ]).mapped('quantity'))
                if float_compare(
                    linked_quantity + rounded_quantity,
                    original_movement.quantity,
                    precision_rounding=uom.rounding,
                ) > 0:
                    raise ValidationError(
                        'A linked movement cannot exceed its original movement.'
                    )
        elif movement_type == 'redeem':
            if source_channel != 'migration':
                raise ValidationError(
                    'New redemption movements must reference an exact issue movement.'
                )
            states = self._fifo_issue_availability(aggregate_line)
            matching_capacity = sum(
                state['available'] for state in states
                if float_compare(
                    state['issue'].unit_value, unit_value,
                    precision_rounding=currency.rounding,
                ) == 0
            )
            if float_compare(
                rounded_quantity, matching_capacity,
                precision_rounding=uom.rounding,
            ) > 0:
                raise ValidationError(
                    'A legacy redemption cannot be matched to unused issue value.'
                )

        movement = self.with_context(**{
            _APPEND_CONTEXT_KEY: _APPEND_TOKEN,
        }).sudo().create({
            **payload,
            'operation_id': operation.id,
            'idempotency_key': idempotency_key,
            'partner_id': partner.id,
            'company_id': company.id,
            'occurred_at': occurred_at or fields.Datetime.now(),
            'source_model': source_model,
            'source_res_id': source_res_id,
            'source_name': source_name,
        })
        clean_movement = self.browse(movement.ids)
        if reconcile:
            aggregate_line._reconcile_projection()
        if owns_operation:
            operation._complete_from_engine({'movement_ids': movement.ids})
        return clean_movement

    @api.model
    def _append_to_operation(self, *, operation, **values):
        """Append under one already-open operation without completing it."""
        return self._append_movement(operation=operation, **values)

    def _safe_source_unavailable_action(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Source unavailable'),
                'message': _('The source record is missing or you do not have access.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_open_source(self):
        self.ensure_one()
        try:
            # Explicitly drop any internal sudo flag before source lookup. The
            # smart button must never elevate source-record access.
            source_model = self.env[self.source_model].with_user(self.env.user)
            source = source_model.browse(self.source_res_id)
            source.check_access('read')
            if not source.exists():
                return self._safe_source_unavailable_action()
            if self.source_model == 'sale.order.line':
                order = source.order_id
                order.check_access('read')
                if not order.exists():
                    return self._safe_source_unavailable_action()
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'sale.order',
                    'res_id': order.id,
                    'view_mode': 'form',
                }
            return {
                'type': 'ir.actions.act_window',
                'res_model': self.source_model,
                'res_id': source.id,
                'view_mode': 'form',
            }
        except (AccessError, KeyError, MissingError):
            return self._safe_source_unavailable_action()
