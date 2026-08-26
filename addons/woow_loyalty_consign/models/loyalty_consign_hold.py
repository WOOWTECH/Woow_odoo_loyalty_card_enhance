from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round


_HOLD_MUTATION_CONTEXT_KEY = '_woow_consign_hold_mutation_token'
_HOLD_MUTATION_TOKEN = object()
_MAX_EXPIRY_BATCH_SIZE = 1000
_EXPIRY_CANDIDATE_SCAN_FACTOR = 10
_MAX_EXPIRY_CANDIDATE_SCAN = (
    _MAX_EXPIRY_BATCH_SIZE * _EXPIRY_CANDIDATE_SCAN_FACTOR
)


class _ReleaseExpiryProbe(Exception):
    """Roll back a successful probe so it cannot retain row locks."""


class _ExpiryCandidateUnavailable(Exception):
    """Abort a probe/formal attempt and identify candidates to exclude."""

    def __init__(self, candidate_ids):
        self.candidate_ids = set(candidate_ids)


def _expiry_candidate_scan_limit(batch_size):
    """Return a bounded candidate window larger than a permitted batch."""
    return min(
        batch_size * _EXPIRY_CANDIDATE_SCAN_FACTOR,
        _MAX_EXPIRY_CANDIDATE_SCAN,
    )


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

    def _lock_active_lifecycle_dimensions(self, company, partner, now=None):
        """Lock one active Hold in the shared lifecycle hierarchy.

        The caller already owns the command idempotency token.  This helper
        acquires original-operation durable tokens, cards, aggregate
        projections, the Hold, issue movements, then allocation rows in
        deterministic order and returns freshly revalidated records.  It
        deliberately uses no ``SKIP LOCKED``: a capture/release command must
        serialize rather than silently skip its own Hold.
        """
        self.ensure_one()
        now = now or fields.Datetime.now()
        hold_id = self.id
        self.env.cr.execute(
            '''SELECT allocation.aggregate_line_id, line.card_id,
                      allocation.issue_movement_id, issue.operation_id,
                      allocation.id
                 FROM loyalty_consign_hold_allocation allocation
                 JOIN loyalty_consign_line line
                   ON line.id = allocation.aggregate_line_id
                 JOIN loyalty_consign_movement issue
                   ON issue.id = allocation.issue_movement_id
                WHERE allocation.hold_id = %s
             ORDER BY issue.operation_id, line.card_id,
                      allocation.aggregate_line_id,
                      allocation.issue_movement_id, allocation.id''',
            (hold_id,),
        )
        dimensions = self.env.cr.fetchall()
        if not dimensions:
            raise ValidationError('An authorization Hold requires allocations.')
        card_ids = sorted({row[1] for row in dimensions})
        line_ids = sorted({row[0] for row in dimensions})
        issue_ids = sorted({row[2] for row in dimensions})
        operation_ids = sorted({row[3] for row in dimensions})
        allocation_ids = sorted({row[4] for row in dimensions})
        movement_model = self.env['loyalty.consign.movement']
        issues = movement_model.sudo().browse(issue_ids)
        # TASK6_CAPTURE_CLAWBACK_LOCK_ORDER: linked-cap commands take every
        # original-operation durable token first, ascending, before any
        # projection token.  This matches _append_movement() for clawback.
        issues_by_operation = {
            operation_id: issues.filtered(
                lambda issue: issue.operation_id.id == operation_id
            )[:1]
            for operation_id in operation_ids
        }
        for operation_id in operation_ids:
            movement_model._lock_original_operation_token(
                issues_by_operation[operation_id]
            )
        for card_id in card_ids:
            self.env.cr.execute(
                '''UPDATE loyalty_card SET write_date = write_date
                    WHERE id = %s RETURNING id''',
                (card_id,),
            )
            if not self.env.cr.fetchone():
                raise ValidationError('The authorization card no longer exists.')
        for line_id in line_ids:
            self.env.cr.execute(
                '''UPDATE loyalty_consign_line SET write_date = write_date
                    WHERE id = %s RETURNING id''',
                (line_id,),
            )
            if not self.env.cr.fetchone():
                raise ValidationError('The authorization projection no longer exists.')
        self.env.cr.execute(
            '''SELECT id FROM loyalty_consign_hold
                WHERE id = %s AND state = 'active' AND expires_at > %s
                FOR UPDATE''',
            (hold_id, now),
        )
        if not self.env.cr.fetchone():
            raise ValidationError('Capture or release requires an active unexpired Hold.')
        self.env.cr.execute(
            '''SELECT id FROM loyalty_consign_movement
                WHERE id = ANY(%s)
             ORDER BY id FOR UPDATE''',
            (issue_ids,),
        )
        if len(self.env.cr.fetchall()) != len(issue_ids):
            raise ValidationError('An authorization issue movement no longer exists.')
        self.env.cr.execute(
            '''SELECT id FROM loyalty_consign_hold_allocation
                WHERE id = ANY(%s) AND hold_id = %s
             ORDER BY id FOR UPDATE''',
            (allocation_ids, hold_id),
        )
        if len(self.env.cr.fetchall()) != len(allocation_ids):
            raise ValidationError('An authorization allocation no longer exists.')
        hold = self.sudo().browse(hold_id).exists()
        allocations = self.env['loyalty.consign.hold.allocation'].sudo().browse(
            allocation_ids
        )
        cards = self.env['loyalty.card'].sudo().browse(card_ids)
        lines = self.env['loyalty.consign.line'].sudo().browse(line_ids)
        cards.invalidate_recordset()
        lines.invalidate_recordset()
        hold.invalidate_recordset()
        if (
            not hold or hold.company_id != company or hold.partner_id != partner
            or hold.state != 'active' or hold.expires_at <= now
            or any(not card.active or not card.is_consign
                   or card.company_id != company
                   or card.program_id.company_id != company
                   or not card.program_id.active
                   or card.program_id.program_type != 'consign'
                   for card in cards)
            or any(line.card_id not in cards or line.partner_id != partner
                   or line.company_id != company for line in lines)
            or any(allocation.hold_id != hold
                   or allocation.aggregate_line_id not in lines
                   or allocation.issue_movement_id not in issues
                   for allocation in allocations)
        ):
            raise ValidationError('The authorization Hold changed while locking.')
        return (
            self.browse(hold.ids),
            self.env['loyalty.consign.hold.allocation'].browse(allocations.ids),
            self.env['loyalty.consign.line'].browse(lines.ids),
        )

    @api.model
    def _expiry_candidate_dimensions(self, candidate_ids):
        """Return every card/projection dimension required by each candidate."""
        dimensions = {
            hold_id: {'cards': set(), 'lines': set()}
            for hold_id in candidate_ids
        }
        self.env.cr.execute(
            '''SELECT allocation.hold_id, allocation.aggregate_line_id, line.card_id
                 FROM loyalty_consign_hold_allocation allocation
                 JOIN loyalty_consign_line line
                   ON line.id = allocation.aggregate_line_id
                WHERE allocation.hold_id = ANY(%s)
             ORDER BY allocation.hold_id, line.card_id, allocation.aggregate_line_id''',
            (candidate_ids,),
        )
        for hold_id, line_id, card_id in self.env.cr.fetchall():
            dimensions[hold_id]['cards'].add(card_id)
            dimensions[hold_id]['lines'].add(line_id)
        return dimensions

    @api.model
    def _lock_expiry_candidates(self, candidate_ids, dimensions, now):
        """Acquire exact candidate dimensions in the global card/line/Hold order."""
        card_ids = sorted({
            card_id for hold_id in candidate_ids
            for card_id in dimensions[hold_id]['cards']
        })
        for card_id in card_ids:
            self.env.cr.execute(
                '''SELECT id FROM loyalty_card WHERE id = %s
                    FOR UPDATE SKIP LOCKED''',
                (card_id,),
            )
            if not self.env.cr.fetchone():
                raise _ExpiryCandidateUnavailable([
                    hold_id for hold_id in candidate_ids
                    if card_id in dimensions[hold_id]['cards']
                ])
        line_ids = sorted({
            line_id for hold_id in candidate_ids
            for line_id in dimensions[hold_id]['lines']
        })
        for line_id in line_ids:
            self.env.cr.execute(
                '''SELECT id FROM loyalty_consign_line WHERE id = %s
                    FOR UPDATE SKIP LOCKED''',
                (line_id,),
            )
            if not self.env.cr.fetchone():
                raise _ExpiryCandidateUnavailable([
                    hold_id for hold_id in candidate_ids
                    if line_id in dimensions[hold_id]['lines']
                ])
        # Allocations are append-only for an active Hold. Still re-read the
        # dimensions under card/line locks to fence stale candidate snapshots.
        if self._expiry_candidate_dimensions(candidate_ids) != {
            hold_id: dimensions[hold_id] for hold_id in candidate_ids
        }:
            raise _ExpiryCandidateUnavailable(candidate_ids)
        for hold_id in sorted(candidate_ids):
            self.env.cr.execute(
                '''SELECT id
                     FROM loyalty_consign_hold
                    WHERE id = %s
                      AND state = 'active' AND expires_at <= %s
                      FOR UPDATE SKIP LOCKED''',
                (hold_id, now),
            )
            if not self.env.cr.fetchone():
                raise _ExpiryCandidateUnavailable([hold_id])

    @api.model
    def _probe_expiry_candidate(self, hold_id, dimensions, now):
        """Test a candidate without retaining any row lock after the probe."""
        try:
            with self.env.cr.savepoint():
                self._lock_expiry_candidates([hold_id], dimensions, now)
                raise _ReleaseExpiryProbe()
        except _ReleaseExpiryProbe:
            return True
        except _ExpiryCandidateUnavailable:
            return False

    def _expire_due_holds(self, now=None):
        """Expire this exact due-Hold set through the shared lock hierarchy.

        Channel lifecycle adapters use this before attempting release so an
        overdue active row cannot strand an otherwise ordinary cart mutation
        while waiting for the periodic cron.
        """
        now = now or fields.Datetime.now()
        due = self.sudo().filtered(
            lambda hold: hold.state == 'active' and hold.expires_at <= now
        )
        if not due:
            return 0
        candidate_ids = sorted(due.ids)
        dimensions = self._expiry_candidate_dimensions(candidate_ids)
        try:
            self._lock_expiry_candidates(candidate_ids, dimensions, now)
        except _ExpiryCandidateUnavailable as unavailable:
            raise ValidationError(
                'The expired authorization Hold is currently changing; retry the cart update.'
            ) from unavailable
        holds = self.sudo().browse(candidate_ids)
        affected = holds.allocation_line_ids.mapped('aggregate_line_id')
        holds.with_context(**{
            _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
        }).sudo().write({
            'state': 'expired',
            'expired_at': now,
            'transition_user_id': self.env.uid,
        })
        affected._reconcile_projection()
        return len(holds)

    @api.model
    def _cron_expire_holds(self, batch_size=100, now=None):
        """Expire a bounded priority batch without retaining rejected candidate locks."""
        batch_size = max(1, min(int(batch_size or 100), _MAX_EXPIRY_BATCH_SIZE))
        now = now or fields.Datetime.now()
        candidate_scan_limit = _expiry_candidate_scan_limit(batch_size)
        self.env.cr.execute(
            '''SELECT id
                 FROM loyalty_consign_hold
                WHERE state = 'active' AND expires_at <= %s
             ORDER BY expires_at, id
                LIMIT %s''',
            (now, candidate_scan_limit),
        )
        candidate_ids = [row[0] for row in self.env.cr.fetchall()]
        if not candidate_ids:
            return 0
        dimensions = self._expiry_candidate_dimensions(candidate_ids)
        remaining = list(candidate_ids)
        while remaining:
            selected = []
            for hold_id in remaining:
                if self._probe_expiry_candidate(hold_id, dimensions, now):
                    selected.append(hold_id)
                    if len(selected) == batch_size:
                        break
            if not selected:
                return 0
            try:
                with self.env.cr.savepoint():
                    self._lock_expiry_candidates(selected, dimensions, now)
                    holds = self.sudo().browse(sorted(selected))
                    affected = holds.allocation_line_ids.mapped('aggregate_line_id')
                    holds.with_context(**{
                        _HOLD_MUTATION_CONTEXT_KEY: _HOLD_MUTATION_TOKEN,
                    }).sudo().write({
                        'state': 'expired',
                        'expired_at': now,
                        'transition_user_id': self.env.uid,
                    })
                    affected._reconcile_projection()
                return len(selected)
            except _ExpiryCandidateUnavailable as unavailable:
                remaining = [
                    hold_id for hold_id in remaining
                    if hold_id not in unavailable.candidate_ids
                ]
        return 0

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
