from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


_HOLD_MUTATION_CONTEXT_KEY = '_woow_consign_hold_mutation_token'
_HOLD_MUTATION_TOKEN = object()


class LoyaltyConsignHold(models.Model):
    _name = 'loyalty.consign.hold'
    _description = 'Consignment Authorization Hold'
    _order = 'expires_at, id'
    _check_company_auto = True

    operation_id = fields.Many2one(
        'loyalty.consign.operation', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, index=True, ondelete='restrict',
    )
    partner_id = fields.Many2one(
        'res.partner', required=True, index=True, ondelete='restrict',
        check_company=True,
    )
    state = fields.Selection(
        [
            ('active', 'Active'),
            ('captured', 'Captured'),
            ('released', 'Released'),
            ('expired', 'Expired'),
        ], required=True, default='active', index=True,
    )
    expires_at = fields.Datetime(required=True, index=True)
    captured_at = fields.Datetime(copy=False)
    released_at = fields.Datetime(copy=False)
    expired_at = fields.Datetime(copy=False)
    transition_user_id = fields.Many2one(
        'res.users', index=True, ondelete='restrict', check_company=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_res_id = fields.Integer(required=True, index=True)
    source_name = fields.Char(required=True)
    idempotency_key = fields.Char(
        related='operation_id.idempotency_key', store=True, index=True,
    )
    allocation_line_ids = fields.One2many(
        'loyalty.consign.hold.allocation', 'hold_id', copy=False,
    )

    _sql_constraints = [
        ('source_res_id_positive', 'CHECK(source_res_id > 0)', 'The Hold source must be valid.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(_HOLD_MUTATION_CONTEXT_KEY) is not _HOLD_MUTATION_TOKEN:
            raise ValidationError('Consignment Holds can only be created by the private engine.')
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get(_HOLD_MUTATION_CONTEXT_KEY) is not _HOLD_MUTATION_TOKEN:
            raise ValidationError('Consignment Holds can only be transitioned by the private engine.')
        return super().write(vals)

    @api.model
    def _create_from_engine(self, vals):
        vals = dict(vals)
        if not vals.get('source_name'):
            operation = self.env['loyalty.consign.operation'].sudo().browse(
                vals.get('operation_id')
            )
            vals['source_name'] = operation.source_name
        hold = self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().create(vals)
        return self.browse(hold.ids)

    def _write_from_engine(self, vals):
        return self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().write(vals)

    @api.constrains(
        'operation_id', 'company_id', 'partner_id', 'source_model', 'source_res_id',
    )
    def _check_operation_dimensions(self):
        for hold in self:
            operation = hold.operation_id
            if (
                hold.company_id != operation.company_id
                or hold.partner_id != operation.partner_id
                or hold.source_model != operation.source_model
                or hold.source_res_id != operation.source_res_id
            ):
                raise ValidationError(
                    'Hold operation, company, partner, and source dimensions must match.'
                )

    def unlink(self):
        raise ValidationError('Consignment Holds are retained for audit and cannot be deleted.')


class LoyaltyConsignHoldAllocation(models.Model):
    _name = 'loyalty.consign.hold.allocation'
    _description = 'Consignment Hold FIFO Allocation'
    _order = 'hold_id, issue_movement_id, id'
    _check_company_auto = True

    hold_id = fields.Many2one(
        'loyalty.consign.hold', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    company_id = fields.Many2one(
        related='hold_id.company_id', store=True, index=True,
    )
    aggregate_line_id = fields.Many2one(
        'loyalty.consign.line', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    issue_movement_id = fields.Many2one(
        'loyalty.consign.movement', required=True, index=True,
        ondelete='restrict', check_company=True,
    )
    card_id = fields.Many2one(
        related='aggregate_line_id.card_id', store=True, index=True,
    )
    product_id = fields.Many2one(
        related='aggregate_line_id.product_id', store=True, index=True,
    )
    product_uom_id = fields.Many2one(
        related='aggregate_line_id.product_uom_id', store=True, index=True,
    )
    quantity = fields.Float(required=True)

    _sql_constraints = [
        ('quantity_positive', 'CHECK(quantity > 0)', 'Hold allocation quantity must be positive.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(_HOLD_MUTATION_CONTEXT_KEY) is not _HOLD_MUTATION_TOKEN:
            raise ValidationError(
                'Consignment Hold allocations can only be created by the private engine.'
            )
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get(_HOLD_MUTATION_CONTEXT_KEY) is not _HOLD_MUTATION_TOKEN:
            raise ValidationError(
                'Consignment Hold allocations can only be changed by the private engine.'
            )
        return super().write(vals)

    @api.model
    def _create_from_engine(self, vals):
        allocation = self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().create(vals)
        return self.browse(allocation.ids)

    def _write_from_engine(self, vals):
        return self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().write(vals)

    @api.constrains('hold_id', 'aggregate_line_id', 'issue_movement_id', 'quantity')
    def _check_allocation_dimensions(self):
        for allocation in self:
            movement = allocation.issue_movement_id
            if movement.movement_type != 'issue':
                raise ValidationError('A Hold allocation must reference an issue movement.')
            if (
                movement.company_id != allocation.company_id
                or movement.partner_id != allocation.hold_id.partner_id
                or movement.aggregate_line_id != allocation.aggregate_line_id
                or movement.card_id != allocation.card_id
                or movement.product_id != allocation.product_id
                or movement.product_uom_id != allocation.product_uom_id
            ):
                raise ValidationError('Hold allocation dimensions must match the issue movement.')
            if float_compare(
                allocation.quantity, 0.0,
                precision_rounding=allocation.product_uom_id.rounding,
            ) <= 0:
                raise ValidationError('Hold allocation quantity must be positive after rounding.')

    def unlink(self):
        raise ValidationError('Consignment Hold allocations cannot be deleted.')
