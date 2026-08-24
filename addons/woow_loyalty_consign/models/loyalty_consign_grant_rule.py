from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


_CONSIGN_TRIGGER_LOCK_NAMESPACE = 1129270867


class LoyaltyConsignGrantRule(models.Model):
    _name = 'loyalty.consign.grant.rule'
    _description = 'Consignment Grant Rule'
    _order = 'program_id, id'
    _rec_name = 'trigger_product_id'
    _check_company_auto = True

    program_id = fields.Many2one(
        'loyalty.program',
        string='寄品方案',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    trigger_product_id = fields.Many2one(
        'product.product',
        string='觸發產品',
        required=True,
        index=True,
        check_company=True,
    )
    grant_line_ids = fields.One2many(
        'loyalty.consign.grant.line',
        'rule_id',
        string='授權品項',
        required=True,
        copy=True,
    )
    company_id = fields.Many2one(
        related='program_id.company_id',
        store=True,
        index=True,
    )

    _sql_constraints = [
        (
            'program_trigger_unique',
            'unique(program_id, trigger_product_id)',
            '同一寄品方案不可重複設定相同的觸發產品。',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        trigger_ids = [
            vals.get('trigger_product_id')
            for vals in vals_list
            if vals.get('trigger_product_id')
        ]
        self._lock_trigger_product_ids(trigger_ids)

        pairs = [
            (vals.get('program_id'), vals.get('trigger_product_id'))
            for vals in vals_list
            if vals.get('program_id') and vals.get('trigger_product_id')
        ]
        if len(pairs) != len(set(pairs)):
            raise ValidationError(
                _('同一寄品方案不可重複設定相同的觸發產品。')
            )
        invariant_rules = self.sudo().with_context(active_test=False)
        for program_id, trigger_product_id in sorted(set(pairs)):
            if invariant_rules.search([
                ('program_id', '=', program_id),
                ('trigger_product_id', '=', trigger_product_id),
            ], limit=1):
                raise ValidationError(
                    _('同一寄品方案不可重複設定相同的觸發產品。')
                )

        rules = super().create(vals_list)
        # One2many constraints are not triggered when grant_line_ids is
        # omitted entirely, so validate the post-create state unconditionally.
        rules._check_has_grant_lines()
        return rules

    @api.constrains('program_id')
    def _check_consign_program(self):
        for rule in self:
            if rule.program_id.program_type != 'consign':
                raise ValidationError(_('授權規則只能屬於寄品方案。'))

    @api.constrains('program_id', 'trigger_product_id')
    def _check_unique_program_trigger(self):
        for rule in self:
            if not rule.program_id or not rule.trigger_product_id:
                continue
            duplicate = self.search([
                ('id', '!=', rule.id),
                ('program_id', '=', rule.program_id.id),
                ('trigger_product_id', '=', rule.trigger_product_id.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    _('同一寄品方案不可重複設定相同的觸發產品。')
                )

    def _trigger_product_lock_ids(self):
        """Return the deterministic advisory-lock order for this recordset."""
        return sorted(set(self.mapped('trigger_product_id').ids))

    @api.model
    def _lock_trigger_product_ids(self, product_ids):
        """Serialize validation for exact trigger IDs before rows exist."""
        for product_id in sorted(set(product_ids)):
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (_CONSIGN_TRIGGER_LOCK_NAMESPACE, product_id),
            )
            # Advisory locks do not refresh an existing repeatable-read
            # snapshot. This intentional no-op tuple update makes a concurrent
            # stale transaction raise SerializationFailure; Odoo retries it
            # with a fresh snapshot, then the invariant search sees the winner.
            self.env.cr.execute(
                'UPDATE product_product '
                'SET write_date = write_date '
                'WHERE id = %s '
                'RETURNING id',
                (product_id,),
            )

    def _lock_trigger_products(self):
        """Serialize overlap validation for each exact trigger product."""
        self._lock_trigger_product_ids(self._trigger_product_lock_ids())

    @api.constrains('program_id', 'trigger_product_id')
    def _check_active_trigger_overlap(self):
        """Reject ambiguous active rules in overlapping company scopes."""
        active_rules = self.filtered(
            lambda rule: (
                rule.program_id.active
                and rule.program_id.program_type == 'consign'
                and rule.trigger_product_id
            )
        )
        active_rules._lock_trigger_products()
        invariant_rules = self.sudo().with_context(active_test=False)

        for rule in active_rules:
            domain = [
                ('id', '!=', rule.id),
                ('trigger_product_id', '=', rule.trigger_product_id.id),
                ('program_id.active', '=', True),
                ('program_id.program_type', '=', 'consign'),
            ]
            if rule.company_id:
                domain += [
                    '|',
                    ('company_id', '=', False),
                    ('company_id', '=', rule.company_id.id),
                ]
            conflict = invariant_rules.search(domain, limit=1)
            if conflict:
                raise ValidationError(_(
                    '觸發產品「%(product)s」已用於公司範圍重疊的有效寄品方案。',
                    product=rule.trigger_product_id.display_name,
                ))

    @api.constrains('grant_line_ids')
    def _check_has_grant_lines(self):
        for rule in self:
            if not rule.grant_line_ids:
                raise ValidationError(_('每個寄品授權規則至少需要一個授權品項。'))
