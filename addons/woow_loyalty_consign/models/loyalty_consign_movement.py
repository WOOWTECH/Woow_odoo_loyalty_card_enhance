from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.tools import float_compare, float_round

from ..hooks import ensure_movement_immutability_trigger


_APPEND_CONTEXT_KEY = '_woow_consign_append_movement_token'
_APPEND_TOKEN = object()


class LoyaltyConsignMovement(models.Model):
    """Immutable consignment fact.

    Task 3 shadows legacy facts. Projection authority remains with the legacy
    line until the engine cut-over in Task 4.
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
    def _append_movement(
        self, *, aggregate_line, movement_type, quantity, source_channel,
        source_model, source_res_id, source_name, idempotency_key,
        occurred_at=None, unit_value=None, original_movement=None,
        product_desc_snapshot=None, lot_snapshot=None, storage_snapshot=None,
        allow_inactive_card=False,
    ):
        """Append one immutable fact or replay the exact prior command."""
        aggregate_line = (
            aggregate_line if hasattr(aggregate_line, 'id')
            else self.env['loyalty.consign.line'].browse(aggregate_line)
        )
        aggregate_line.ensure_one()
        card = aggregate_line.card_id
        company = (
            card.company_id or card.program_id.company_id
            or aggregate_line.sale_order_id.company_id or self.env.company
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

        if unit_value is None:
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

        if original_movement:
            # Lock order is command advisory/company token, then the original
            # operation token, then linked search/create. The durable no-op
            # update turns a stale distinct-key snapshot into PostgreSQL's
            # SerializationFailure; the outer Odoo request retry must handle it.
            self._lock_original_operation_token(original_movement)
            if (
                movement_type == 'issue_reversal'
                and original_movement.sudo().hold_allocation_ids.filtered(
                    lambda allocation: allocation.hold_id.state == 'active'
                )
            ):
                raise ValidationError(
                    'An issue with an active Consignment Hold allocation cannot be reversed.'
                )
            linked_quantity = sum(self.sudo().search([
                ('original_movement_id', '=', original_movement.id),
                ('movement_type', '=', movement_type),
            ]).mapped('quantity'))
            if float_compare(
                linked_quantity + rounded_quantity,
                original_movement.quantity,
                precision_rounding=uom.rounding,
            ) > 0:
                # This exception intentionally rolls back a newly-created
                # pending operation together with the failed request.
                raise ValidationError('A linked movement cannot exceed its original movement.')

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
        operation.sudo().write({
            'state': 'done',
            'completed_at': fields.Datetime.now(),
            'result_json': {'movement_ids': movement.ids},
        })
        return self.browse(movement.ids)

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
