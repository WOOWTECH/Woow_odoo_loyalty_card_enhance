from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class LoyaltyConsignRedemption(models.Model):
    """Manager-authored audit document backed exclusively by engine commands."""

    _name = 'loyalty.consign.redemption'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _description = '寄品核銷單'
    _order = 'date_redemption desc, id desc'
    _rec_name = 'name'
    _check_company_auto = True

    name = fields.Char(string='核銷單號', readonly=True, copy=False, default='/')
    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True, check_company=True,
        domain=[('is_consign', '=', True)],
    )
    company_id = fields.Many2one(
        related='card_id.company_id', store=True, string='公司', index=True,
    )
    partner_id = fields.Many2one(related='card_id.partner_id', store=True, string='客戶')
    date_redemption = fields.Datetime(string='核銷日期', default=fields.Datetime.now)
    staff_user_id = fields.Many2one(
        'res.users', string='服務人員', default=lambda self: self.env.user,
        check_company=True,
    )
    service_note = fields.Text(string='服務備註', help='施打劑量、操作師、核銷原因')
    submission_uuid = fields.Char(string='Submission UUID', copy=False, index=True)
    authorization_operation_id = fields.Many2one(
        'loyalty.consign.operation', string='Authorization Operation', readonly=True,
        copy=False, check_company=True, ondelete='restrict',
    )
    capture_operation_id = fields.Many2one(
        'loyalty.consign.operation', string='Capture Operation', readonly=True,
        copy=False, check_company=True, ondelete='restrict',
    )
    movement_ids = fields.One2many(
        related='capture_operation_id.movement_ids', string='Ledger Movements', readonly=True,
    )
    state = fields.Selection(
        [('draft', '草稿'), ('done', '已完成')], string='狀態', default='draft',
        readonly=True, copy=False, tracking=True,
    )
    line_ids = fields.One2many('loyalty.consign.redemption.line', 'redemption_id', string='核銷明細')
    total_redeemed_value = fields.Monetary(
        string='核銷總金額', compute='_compute_total_redeemed_value', currency_field='currency_id',
    )
    currency_id = fields.Many2one(related='card_id.currency_id', store=True)

    _sql_constraints = [
        (
            'company_submission_uuid_unique',
            'unique(company_id, submission_uuid)',
            'A consignment submission UUID may only create one document per company.',
        ),
    ]

    @api.model
    def _require_manager(self):
        if not self.env.is_superuser() and not self.env.user.has_group(
            'woow_loyalty_consign.group_consign_manager'
        ):
            raise AccessError('Only Consign Managers may execute manual consignment commands.')

    def _compute_access_url(self):
        super()._compute_access_url()
        for rec in self:
            rec.access_url = f'/my/consign-redemptions/{rec.id}'

    @api.depends('capture_operation_id.movement_ids.value_delta', 'line_ids.subtotal')
    def _compute_total_redeemed_value(self):
        for rec in self:
            # The ledger is authoritative as soon as capture succeeds. Draft
            # lines retain a UI preview only and never determine posted value.
            rec.total_redeemed_value = sum(rec.movement_ids.mapped('value_delta')) if rec.capture_operation_id else sum(rec.line_ids.mapped('subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_manager()
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('loyalty.consign.redemption') or '/'
            if vals.get('state', 'draft') != 'draft':
                raise ValidationError('A consignment redemption document must start in draft.')
        return super().create(vals_list)

    def write(self, vals):
        self._require_manager()
        if self.filtered(lambda redemption: redemption.state == 'done'):
            raise ValidationError('Completed redemption audit records are immutable.')
        if 'state' in vals and vals['state'] not in ('draft', 'done'):
            raise ValidationError('Invalid consignment redemption state.')
        return super().write(vals)

    def unlink(self):
        self._require_manager()
        if self.filtered(lambda redemption: redemption.state == 'done'):
            raise ValidationError('Completed redemption audit records cannot be deleted.')
        return super().unlink()

    def _requests_from_lines(self):
        self.ensure_one()
        if not self.line_ids:
            raise ValidationError('A redemption document requires at least one line.')
        requests = []
        for line in self.line_ids:
            projection = line.consign_line_id
            if projection.card_id != self.card_id:
                raise ValidationError('Every redemption line must belong to the document card.')
            requests.append({
                'card': self.card_id,
                'product': projection.product_id,
                'uom': projection.product_uom_id,
                'quantity': line.qty_redeemed,
            })
        return requests

    def action_done(self):
        """Authorize then capture; this document is never a second ledger."""
        self._require_manager()
        for rec in self:
            if rec.state == 'done':
                continue
            if not (rec.service_note or '').strip():
                raise ValidationError('A service note/reason is required before capture.')
            if not (rec.submission_uuid or '').strip():
                raise ValidationError('A stable submission UUID is required before capture.')
            if not rec.card_id.is_consign or not rec.card_id.active:
                raise ValidationError('Manual redemption requires an active consignment card.')
            requests = rec._requests_from_lines()
            key_prefix = f'consign:backend:redemption:v1:{rec.submission_uuid}'
            engine = self.env['loyalty.consign.engine']
            authorization = engine._authorize(
                source=rec, partner=rec.partner_id, requests=requests,
                idempotency_key=f'{key_prefix}:authorize',
            )
            capture = engine._capture(
                source=rec, partner=rec.partner_id, hold=authorization.hold_ids,
                idempotency_key=f'{key_prefix}:capture',
            )
            # The document snapshots the completed engine result for display;
            # it never selects FIFO facts or mutates them.  A one-line backend
            # document can be shown with its exact normalized quantity/value.
            if len(rec.line_ids) == 1:
                movement_value = sum(capture.movement_ids.mapped('value_delta'))
                movement_quantity = sum(capture.movement_ids.mapped('quantity'))
                rec.line_ids.write({
                    'qty_redeemed': movement_quantity,
                    'unit_price': (
                        movement_value / movement_quantity if movement_quantity else 0.0
                    ),
                    'subtotal': movement_value,
                })
            rec.write({
                'authorization_operation_id': authorization.id,
                'capture_operation_id': capture.id,
                'state': 'done',
            })
            rec.card_id.message_post(
                body=f'核銷單 {rec.name} 已完成，共核銷 {len(rec.line_ids)} 筆品項。',
                message_type='notification',
            )
        return True


class LoyaltyConsignRedemptionLine(models.Model):
    _name = 'loyalty.consign.redemption.line'
    _description = '寄品核銷明細'
    _rec_name = 'product_desc'
    _check_company_auto = True

    redemption_id = fields.Many2one(
        'loyalty.consign.redemption', string='核銷單', required=True,
        ondelete='cascade', check_company=True,
    )
    company_id = fields.Many2one(related='redemption_id.company_id', store=True, index=True)
    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='寄品明細', required=True, check_company=True,
    )
    product_id = fields.Many2one(related='consign_line_id.product_id', store=True, string='品項')
    product_uom_id = fields.Many2one(related='consign_line_id.product_uom_id', store=True, string='計量單位')
    product_desc = fields.Char(related='consign_line_id.product_desc', store=True, string='品項說明')
    qty_available = fields.Float(related='consign_line_id.qty_available', string='可用數量')
    qty_redeemed = fields.Float(string='本次核銷數量')
    unit_price = fields.Float(string='單價', readonly=True, copy=False)
    subtotal = fields.Monetary(string='小計', currency_field='currency_id', readonly=True, copy=False)
    currency_id = fields.Many2one(related='redemption_id.currency_id', store=True)
    note = fields.Char(string='備註')

    @api.model_create_multi
    def create(self, vals_list):
        self.env['loyalty.consign.redemption']._require_manager()
        for vals in vals_list:
            redemption = self.env['loyalty.consign.redemption'].browse(vals.get('redemption_id'))
            if redemption and redemption.state == 'done':
                raise ValidationError('Lines cannot be added to a completed redemption audit.')
            line = self.env['loyalty.consign.line'].browse(vals.get('consign_line_id'))
            quantity = vals.get('qty_redeemed', 0.0)
            vals.setdefault('unit_price', line.unit_price if line else 0.0)
            vals.setdefault('subtotal', (line.currency_id or self.env.company.currency_id).round(quantity * vals['unit_price']))
        return super().create(vals_list)

    def write(self, vals):
        self.env['loyalty.consign.redemption']._require_manager()
        if self.filtered(lambda line: line.redemption_id.state == 'done'):
            raise ValidationError('Completed redemption audit lines are immutable.')
        return super().write(vals)

    def unlink(self):
        self.env['loyalty.consign.redemption']._require_manager()
        if self.filtered(lambda line: line.redemption_id.state == 'done'):
            raise ValidationError('Completed redemption audit lines cannot be deleted.')
        return super().unlink()

    @api.constrains('qty_redeemed')
    def _check_qty_redeemed(self):
        for line in self:
            if line.qty_redeemed <= 0:
                raise ValidationError('The redemption quantity must be positive.')
