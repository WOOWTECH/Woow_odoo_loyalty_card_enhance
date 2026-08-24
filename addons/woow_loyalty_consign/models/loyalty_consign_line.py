from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare, float_round


_SPECIFIC_CREATE_CONTEXT_KEY = '_woow_consign_specific_movement_create_token'
_SPECIFIC_CREATE_TOKEN = object()
_CANCEL_CONTEXT_KEY = '_woow_consign_cancel_token'
_CANCEL_TOKEN = object()


class LoyaltyConsignLine(models.Model):
    _name = 'loyalty.consign.line'
    _description = '寄品明細'
    _order = 'date_deposited desc, id desc'
    _rec_name = 'product_desc'
    _check_company_auto = True

    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True, ondelete='cascade',
        index=True, check_company=True,
    )
    company_id = fields.Many2one(
        related='card_id.company_id', store=True, string='公司', index=True,
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', store=True, string='客戶', index=True,
    )
    program_id = fields.Many2one(
        related='card_id.program_id', store=True, string='寄品方案', index=True,
    )
    currency_id = fields.Many2one(
        related='card_id.currency_id', store=True,
    )
    product_id = fields.Many2one(
        'product.product', string='品項', required=True, check_company=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='計量單位', required=True, index=True,
        default=lambda self: self.env.ref('uom.product_uom_unit'),
    )
    product_desc = fields.Char(
        string='品項說明',
        help='自訂顯示名稱，例如酒款年份、療程細項',
    )
    lot_id = fields.Many2one(
        'stock.lot', string='批號', check_company=True,
    )
    storage_note = fields.Char(
        string='儲位備註', help='例如酒窖 A-03',
    )
    qty_deposited = fields.Float(string='存入數量', default=1.0)
    qty_redeemed = fields.Float(
        string='已核銷數量', compute='_compute_qty_redeemed', store=True,
    )
    qty_remaining = fields.Float(
        string='舊版剩餘數量', compute='_compute_qty_remaining', store=True,
        help='相容既有核銷資料的歷史投影；現行可用餘額請使用「可用數量」。',
    )
    unit_price = fields.Float(string='單價')
    amount_deposited = fields.Monetary(
        string='存入金額', compute='_compute_amounts', store=True,
        currency_field='currency_id',
    )
    amount_remaining = fields.Monetary(
        string='舊版剩餘金額', compute='_compute_amounts', store=True,
        currency_field='currency_id',
        help='依舊版剩餘數量保留的歷史相容值，不代表現行可用金額。',
    )
    date_deposited = fields.Date(
        string='存入日期', default=fields.Date.context_today,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line', string='來源訂單行', ondelete='set null',
        check_company=True,
    )
    sale_order_id = fields.Many2one(
        related='sale_line_id.order_id', store=True, string='來源訂單',
    )
    is_cancelled = fields.Boolean(string='已取消', default=False)
    state = fields.Selection(
        [('active', '有效'), ('depleted', '已用完'), ('cancelled', '已取消')],
        string='狀態', compute='_compute_state', store=True, default='active',
    )
    redemption_line_ids = fields.One2many(
        'loyalty.consign.redemption.line', 'consign_line_id', string='核銷紀錄',
    )
    movement_ids = fields.One2many(
        'loyalty.consign.movement', 'aggregate_line_id', string='Ledger Movements',
        copy=False,
    )
    qty_issued = fields.Float(
        compute='_compute_shadow_quantities', store=True, string='Issued',
    )
    qty_reversed = fields.Float(
        compute='_compute_shadow_quantities', store=True, string='Reversed',
    )
    qty_revoked = fields.Float(
        compute='_compute_shadow_quantities', store=True, string='Revoked',
    )
    qty_on_hold = fields.Float(
        compute='_compute_shadow_quantities', store=True, string='On Hold',
    )
    qty_available = fields.Float(
        compute='_compute_shadow_quantities', store=True, string='Available',
    )

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for original_vals in vals_list:
            vals = dict(original_vals)
            if vals.get('product_id') and not vals.get('product_uom_id'):
                vals['product_uom_id'] = self.env['product.product'].browse(
                    vals['product_id']
                ).uom_id.id
            normalized.append(vals)
        lines = super().create(normalized)
        if self.env.context.get(_SPECIFIC_CREATE_CONTEXT_KEY) is not _SPECIFIC_CREATE_TOKEN:
            movement_model = self.env['loyalty.consign.movement']
            for line in lines:
                movement_model._append_movement(
                    aggregate_line=line,
                    movement_type='issue',
                    quantity=line.qty_deposited,
                    source_channel='manual',
                    source_model='loyalty.consign.line',
                    source_res_id=line.id,
                    source_name=line.display_name,
                    idempotency_key=f'consign:legacy-line:v1:{line.id}:issue',
                )
        return lines

    @api.model
    def _create_for_specific_movement(self, vals):
        """Internal creation seam for callers that append a richer issue fact."""
        line = self.with_context(**{
            _SPECIFIC_CREATE_CONTEXT_KEY: _SPECIFIC_CREATE_TOKEN,
        }).create(vals)
        return self.browse(line.ids)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
            if not self.product_desc:
                self.product_desc = self.product_id.name
            if not self.unit_price:
                self.unit_price = self.product_id.list_price

    @api.constrains('qty_deposited')
    def _check_qty_deposited(self):
        for line in self:
            if line.qty_deposited <= 0:
                raise ValidationError('存入數量必須大於 0。')

    @api.constrains('product_id', 'product_uom_id')
    def _check_product_uom_category(self):
        for line in self:
            if (
                line.product_id and line.product_uom_id
                and line.product_id.uom_id.category_id != line.product_uom_id.category_id
            ):
                raise ValidationError('品項與計量單位必須屬於相同類別。')

    @api.depends('redemption_line_ids.qty_redeemed', 'redemption_line_ids.redemption_id.state')
    def _compute_qty_redeemed(self):
        for line in self:
            line.qty_redeemed = sum(
                redemption_line.qty_redeemed
                for redemption_line in line.redemption_line_ids
                if redemption_line.redemption_id.state == 'done'
            )

    @api.depends('qty_deposited', 'qty_redeemed')
    def _compute_qty_remaining(self):
        for line in self:
            line.qty_remaining = max(0, line.qty_deposited - line.qty_redeemed)

    @api.depends('qty_deposited', 'qty_remaining', 'unit_price')
    def _compute_amounts(self):
        for line in self:
            line.amount_deposited = float_round(
                line.qty_deposited * line.unit_price, precision_digits=2,
            )
            line.amount_remaining = float_round(
                line.qty_remaining * line.unit_price, precision_digits=2,
            )

    @api.depends('qty_available', 'is_cancelled')
    def _compute_state(self):
        for line in self:
            if line.is_cancelled:
                line.state = 'cancelled'
            elif line.qty_available <= 0:
                line.state = 'depleted'
            else:
                line.state = 'active'

    @api.depends(
        'movement_ids.quantity', 'movement_ids.movement_type',
        'movement_ids.original_movement_id',
        'movement_ids.hold_allocation_ids.quantity',
        'movement_ids.hold_allocation_ids.hold_id.state',
    )
    def _compute_shadow_quantities(self):
        for line in self:
            by_type = {
                movement_type: sum(line.movement_ids.filtered(
                    lambda movement, kind=movement_type: movement.movement_type == kind
                ).mapped('quantity'))
                for movement_type in (
                    'issue', 'redeem', 'redeem_reversal', 'issue_reversal',
                    'adjustment_in', 'adjustment_out',
                )
            }
            active_allocations = line.movement_ids.mapped('hold_allocation_ids').filtered(
                lambda allocation: allocation.hold_id.state == 'active'
            )
            line.qty_issued = by_type['issue']
            line.qty_reversed = by_type['redeem_reversal']
            line.qty_revoked = by_type['issue_reversal']
            line.qty_on_hold = sum(active_allocations.mapped('quantity'))
            line.qty_available = (
                by_type['issue'] + by_type['redeem_reversal']
                + by_type['adjustment_in'] - by_type['redeem']
                - by_type['issue_reversal'] - by_type['adjustment_out']
                - line.qty_on_hold
            )

    def _write_accumulate(self, vals):
        return super().write(vals)

    def _write_schema_backfill(self, vals):
        return super().write(vals)

    def write(self, vals):
        protected = {
            'card_id', 'sale_line_id', 'date_deposited', 'lot_id',
            'storage_note', 'is_cancelled',
            'qty_deposited', 'qty_redeemed', 'qty_issued', 'qty_reversed',
            'qty_revoked', 'qty_on_hold', 'qty_available',
            'product_id', 'product_uom_id', 'unit_price', 'product_desc',
        }
        changed_protected = protected & set(vals)
        cancellation_write = (
            changed_protected == {'is_cancelled'}
            and self.env.context.get(_CANCEL_CONTEXT_KEY) is _CANCEL_TOKEN
        )
        if changed_protected and not cancellation_write:
            for line in self:
                if not isinstance(line.id, models.NewId) and line.sudo().movement_ids:
                    raise ValidationError(
                        '已有動帳紀錄的寄品明細不可修改（%s）。\n'
                        '如需扣除請使用「核銷」功能，如需新增請用「加入資料行」。'
                        % ', '.join(sorted(changed_protected))
                    )
        return super().write(vals)

    def _write_cancelled(self):
        """Set the legacy cancellation projection under caller authorization."""
        return self.with_context(**{
            _CANCEL_CONTEXT_KEY: _CANCEL_TOKEN,
        }).write({'is_cancelled': True})

    def unlink(self):
        for line in self:
            if line.movement_ids:
                raise ValidationError(
                    '已有不可變動帳紀錄的寄品明細不可刪除（%s），請使用取消功能。'
                    % (line.product_desc or line.product_id.name)
                )
            if line.qty_redeemed > 0:
                raise ValidationError(
                    '已有核銷紀錄的寄品明細不可刪除（%s）。'
                    % (line.product_desc or line.product_id.name)
                )
        return super().unlink()

    def _append_issue_reversal_for_remaining(
        self, *, source_channel, key_prefix, allow_inactive_card=False,
    ):
        self.ensure_one()
        active_allocations = self.sudo().movement_ids.mapped(
            'hold_allocation_ids'
        ).filtered(lambda allocation: allocation.hold_id.state == 'active')
        if active_allocations:
            raise ValidationError(
                'A consignment line with an active Hold allocation cannot be cancelled or reversed.'
            )
        movement_model = self.env['loyalty.consign.movement']
        issues = self.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue'
        ).sorted(lambda movement: (movement.occurred_at, movement.id))
        consumed = self.qty_redeemed
        appended = movement_model.browse()
        sequence = 0
        for issue in issues:
            already_reversed = sum(self.movement_ids.filtered(
                lambda movement: (
                    movement.movement_type == 'issue_reversal'
                    and movement.original_movement_id == issue
                )
            ).mapped('quantity'))
            issue_unreversed = max(0, issue.quantity - already_reversed)
            consumed_here = min(consumed, issue_unreversed)
            consumed -= consumed_here
            reversible = issue_unreversed - consumed_here
            if float_compare(
                reversible, 0.0, precision_rounding=self.product_uom_id.rounding,
            ) <= 0:
                continue
            sequence += 1
            appended |= movement_model._append_movement(
                aggregate_line=self,
                movement_type='issue_reversal',
                quantity=reversible,
                source_channel=source_channel,
                source_model='loyalty.consign.line',
                source_res_id=self.id,
                source_name=self.display_name,
                idempotency_key=f'{key_prefix}:{sequence}:{issue.id}',
                original_movement=issue,
                allow_inactive_card=allow_inactive_card,
            )
        return appended

    def action_cancel(self):
        # This is a public model action: portal ownership/read access is never
        # authority to append cancellation facts or change the legacy state.
        self.check_access('write')
        if (
            not self.env.is_superuser()
            and not self.env.user.has_group('sales_team.group_sale_manager')
        ):
            raise AccessError(_('Only Sales managers may cancel consignment lines.'))

        lines_to_cancel = self.filtered(lambda line: line.state != 'cancelled')
        for line in lines_to_cancel:
            if line.sudo().movement_ids.mapped('hold_allocation_ids').filtered(
                lambda allocation: allocation.hold_id.state == 'active'
            ):
                raise ValidationError(
                    'A consignment line with an active Hold allocation cannot be cancelled.'
                )
            if float_compare(
                line.qty_available, 0.0,
                precision_rounding=line.product_uom_id.rounding,
            ) <= 0:
                raise ValidationError('已無剩餘數量可取消。')
            line._append_issue_reversal_for_remaining(
                source_channel='manual',
                key_prefix=f'consign:legacy-cancellation:v1:{line.id}',
            )
        if lines_to_cancel:
            lines_to_cancel._write_cancelled()
