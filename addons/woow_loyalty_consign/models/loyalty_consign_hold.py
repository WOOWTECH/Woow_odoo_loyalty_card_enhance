from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round


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
        affected = self.sudo().allocation_line_ids.mapped('aggregate_line_id')
        result = self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().write(vals)
        affected._reconcile_projection()
        return result

    @api.model
    def _cron_expire_holds(self, batch_size=100, now=None):
        """Expire one bounded deterministic batch without contending workers."""
        batch_size = max(1, min(int(batch_size or 100), 1000))
        now = now or fields.Datetime.now()
        # Discovering candidates does not lock them. Lock dimensions first, then
        # acquire eligible Hold rows with SKIP LOCKED in the global hierarchy.
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_hold
                WHERE state = 'active' AND expires_at <= %s
             ORDER BY id
                LIMIT %s''',
            (now, batch_size),
        )
        candidate_ids = [row[0] for row in self.env.cr.fetchall()]
        if not candidate_ids:
            return 0
        self.env.cr.execute(
            '''SELECT DISTINCT allocation.aggregate_line_id
                 FROM loyalty_consign_hold_allocation allocation
                WHERE allocation.hold_id = ANY(%s)
             ORDER BY allocation.aggregate_line_id''',
            (candidate_ids,),
        )
        line_ids = [row[0] for row in self.env.cr.fetchall()]
        if line_ids:
            self.env.cr.execute(
                '''SELECT DISTINCT line.card_id
                     FROM loyalty_consign_line line
                    WHERE line.id = ANY(%s)
                 ORDER BY line.card_id''',
                (line_ids,),
            )
            card_ids = [row[0] for row in self.env.cr.fetchall()]
            for card_id in card_ids:
                self.env.cr.execute(
                    '''SELECT id FROM loyalty_card WHERE id = %s
                        FOR UPDATE SKIP LOCKED''',
                    (card_id,),
                )
                if not self.env.cr.fetchone():
                    return 0
            for line_id in sorted(line_ids):
                self.env.cr.execute(
                    '''SELECT id FROM loyalty_consign_line WHERE id = %s
                        FOR UPDATE SKIP LOCKED''',
                    (line_id,),
                )
                if not self.env.cr.fetchone():
                    return 0
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_hold
                WHERE id = ANY(%s)
                  AND state = 'active' AND expires_at <= %s
             ORDER BY id
                  FOR UPDATE SKIP LOCKED''',
            (candidate_ids, now),
        )
        hold_ids = [row[0] for row in self.env.cr.fetchall()]
        if not hold_ids:
            return 0
        holds = self.sudo().browse(hold_ids)
        affected = holds.allocation_line_ids.mapped('aggregate_line_id')
        holds.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().write({
            'state': 'expired',
            'expired_at': now,
            'transition_user_id': self.env.uid,
        })
        affected._reconcile_projection()
        return len(hold_ids)

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
    def _lock_allocation_dimensions(self, issue_movements, aggregate_lines):
        """Lock original operation tuples before deterministic projections."""
        movement_model = self.env['loyalty.consign.movement']
        seen_operations = set()
        for issue in issue_movements.sorted(
            lambda movement: (movement.operation_id.id, movement.id)
        ):
            if issue.operation_id.id not in seen_operations:
                movement_model._lock_original_operation_token(issue)
                seen_operations.add(issue.operation_id.id)
        for line in aggregate_lines.sorted('id'):
            self.env.cr.execute(
                '''UPDATE loyalty_consign_line SET write_date = write_date
                    WHERE id = %s RETURNING id''',
                (line.id,),
            )
            if not self.env.cr.fetchone():
                raise ValidationError('The allocation projection no longer exists.')
        aggregate_lines.invalidate_recordset()
        aggregate_lines._reconcile_projection()

    @api.model
    def _validate_active_capacity(
        self, *, hold, line, issue, quantity, exclude_allocation=None,
    ):
        if hold.state != 'active':
            raise ValidationError('Only an active Hold may receive an allocation.')
        if issue.movement_type != 'issue':
            raise ValidationError('A Hold allocation must reference an issue movement.')
        if (
            issue.company_id != hold.company_id
            or issue.partner_id != hold.partner_id
            or issue.aggregate_line_id != line
            or issue.card_id != line.card_id
            or issue.product_id != line.product_id
            or issue.product_uom_id != line.product_uom_id
        ):
            raise ValidationError('Hold allocation dimensions must match the issue movement.')
        rounded = float_round(
            quantity, precision_rounding=line.product_uom_id.rounding,
        )
        if float_compare(
            rounded, 0.0, precision_rounding=line.product_uom_id.rounding,
        ) <= 0:
            raise ValidationError('Hold allocation quantity must be positive after rounding.')
        domain = [
            ('issue_movement_id', '=', issue.id),
            ('hold_id.state', '=', 'active'),
        ]
        if exclude_allocation:
            domain.append(('id', '!=', exclude_allocation.id))
        allocated = sum(self.sudo().search(domain).mapped('quantity'))
        issue_states = self.env[
            'loyalty.consign.movement'
        ]._fifo_issue_availability(line, include_active_holds=False)
        issue_capacity = next(
            (state['available'] for state in issue_states
             if state['issue'] == issue),
            0.0,
        )
        if float_compare(
            allocated + rounded, issue_capacity,
            precision_rounding=line.product_uom_id.rounding,
        ) > 0:
            raise ValidationError(
                'Active Hold allocations cannot exceed the unused issue quantity.'
            )
        available = line.qty_available
        if exclude_allocation and exclude_allocation.hold_id.state == 'active':
            available += exclude_allocation.quantity
        if float_compare(
            rounded, available,
            precision_rounding=line.product_uom_id.rounding,
        ) > 0:
            raise ValidationError(
                'A Hold allocation cannot exceed authoritative available quantity.'
            )
        return rounded

    @api.model
    def _create_planned_from_engine(self, hold, plan):
        """Insert a fully prevalidated engine plan after all hierarchy locks."""
        hold = hold.sudo().exists()
        hold.ensure_one()
        if hold.state != 'active' or not plan:
            raise ValidationError('An active Hold and allocation plan are required.')
        vals_list = []
        for item in plan:
            line = self.env['loyalty.consign.line'].sudo().browse(
                item['aggregate_line_id']
            ).exists()
            issue = self.env['loyalty.consign.movement'].sudo().browse(
                item['issue_movement_id']
            ).exists()
            line.ensure_one()
            issue.ensure_one()
            rounded = float_round(
                item['quantity'], precision_rounding=line.product_uom_id.rounding,
            )
            if float_compare(
                rounded, 0.0, precision_rounding=line.product_uom_id.rounding,
            ) <= 0 or (
                issue.movement_type != 'issue'
                or issue.aggregate_line_id != line
                or issue.company_id != hold.company_id
                or issue.partner_id != hold.partner_id
            ):
                raise ValidationError('The prevalidated Hold allocation plan is invalid.')
            # Retain the Task 4 issue/projection cap as defense in depth. Every
            # row is checked before the first bulk insert, so this cannot make
            # authorization partially visible.
            rounded = self._validate_active_capacity(
                hold=hold, line=line, issue=issue, quantity=rounded,
            )
            vals_list.append({
                'hold_id': hold.id,
                'aggregate_line_id': line.id,
                'issue_movement_id': issue.id,
                'quantity': rounded,
            })
        allocations = self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().create(vals_list)
        clean_allocations = self.browse(allocations.ids)
        clean_allocations.mapped('aggregate_line_id')._reconcile_projection()
        return clean_allocations

    @api.model
    def _create_from_engine(self, vals):
        vals = dict(vals)
        hold = self.env['loyalty.consign.hold'].sudo().browse(vals.get('hold_id')).exists()
        line = self.env['loyalty.consign.line'].sudo().browse(
            vals.get('aggregate_line_id')
        ).exists()
        issue = self.env['loyalty.consign.movement'].sudo().browse(
            vals.get('issue_movement_id')
        ).exists()
        if not hold or not line or not issue:
            raise ValidationError('Hold, projection, and issue movement are required.')
        hold.ensure_one()
        line.ensure_one()
        issue.ensure_one()
        self._lock_allocation_dimensions(issue, line)
        vals['quantity'] = self._validate_active_capacity(
            hold=hold, line=line, issue=issue,
            quantity=vals.get('quantity', 0.0),
        )
        allocation = self.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().create(vals)
        clean_allocation = self.browse(allocation.ids)
        line._reconcile_projection()
        return clean_allocation

    def _write_from_engine(self, vals):
        if set(vals) - {'quantity'}:
            raise ValidationError(
                'Task 4 only permits private Hold allocation quantity repair.'
            )
        allocations = self.sudo().sorted(
            lambda allocation: (
                allocation.issue_movement_id.operation_id.id,
                allocation.aggregate_line_id.id,
                allocation.id,
            )
        )
        self._lock_allocation_dimensions(
            allocations.mapped('issue_movement_id'),
            allocations.mapped('aggregate_line_id'),
        )
        for allocation in allocations:
            quantity = self._validate_active_capacity(
                hold=allocation.hold_id,
                line=allocation.aggregate_line_id,
                issue=allocation.issue_movement_id,
                quantity=vals.get('quantity', allocation.quantity),
                exclude_allocation=allocation,
            )
            allocation.with_context(**{
                _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
            }).sudo().write({'quantity': quantity})
            allocation.aggregate_line_id._reconcile_projection()
        allocations.mapped('aggregate_line_id')._reconcile_projection()
        return True

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
