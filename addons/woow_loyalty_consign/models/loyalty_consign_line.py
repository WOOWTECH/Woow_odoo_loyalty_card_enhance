from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class LoyaltyConsignLine(models.Model):
    _name = 'loyalty.consign.line'
    _description = '寄品明細'
    _order = 'date_deposited desc, id desc'
    _rec_name = 'product_desc'

    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True, ondelete='cascade', index=True,
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', store=True, string='客戶',
    )
    program_id = fields.Many2one(
        related='card_id.program_id', store=True, string='寄品方案',
    )
    currency_id = fields.Many2one(
        related='card_id.currency_id', store=True,
    )
    product_id = fields.Many2one(
        'product.product', string='品項', required=True,
    )
    product_desc = fields.Char(
        string='品項說明',
        help='自訂顯示名稱，例如酒款年份、療程細項',
    )
    lot_id = fields.Many2one(
        'stock.lot', string='批號',
    )
    storage_note = fields.Char(
        string='儲位備註',
        help='例如酒窖 A-03',
    )
    qty_deposited = fields.Float(
        string='存入數量', default=1.0,
    )
    qty_redeemed = fields.Float(
        string='已核銷數量', compute='_compute_qty_redeemed', store=True,
    )
    qty_remaining = fields.Float(
        string='剩餘數量', compute='_compute_qty_remaining', store=True,
    )
    unit_price = fields.Float(
        string='單價',
    )
    amount_deposited = fields.Monetary(
        string='存入金額', compute='_compute_amounts', store=True, currency_field='currency_id',
    )
    amount_remaining = fields.Monetary(
        string='剩餘金額', compute='_compute_amounts', store=True, currency_field='currency_id',
    )
    date_deposited = fields.Date(
        string='存入日期', default=fields.Date.context_today,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line', string='來源訂單行', ondelete='set null',
    )
    sale_order_id = fields.Many2one(
        related='sale_line_id.order_id', store=True, string='來源訂單',
    )
    is_cancelled = fields.Boolean(
        string='已取消', default=False,
    )
    state = fields.Selection(
        [
            ('active', '有效'),
            ('depleted', '已用完'),
            ('cancelled', '已取消'),
        ],
        string='狀態', compute='_compute_state', store=True, default='active',
    )
    redemption_line_ids = fields.One2many(
        'loyalty.consign.redemption.line', 'consign_line_id', string='核銷紀錄',
    )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """選擇產品時自動帶入名稱和單價。"""
        if self.product_id:
            if not self.product_desc:
                self.product_desc = self.product_id.name
            if not self.unit_price:
                self.unit_price = self.product_id.list_price

    @api.constrains('qty_deposited')
    def _check_qty_deposited(self):
        for line in self:
            if line.qty_deposited <= 0:
                raise ValidationError('存入數量必須大於 0。')

    @api.depends('redemption_line_ids.qty_redeemed', 'redemption_line_ids.redemption_id.state')
    def _compute_qty_redeemed(self):
        for line in self:
            line.qty_redeemed = sum(
                rl.qty_redeemed
                for rl in line.redemption_line_ids
                if rl.redemption_id.state == 'done'
            )

    @api.depends('qty_deposited', 'qty_redeemed')
    def _compute_qty_remaining(self):
        for line in self:
            line.qty_remaining = max(0, line.qty_deposited - line.qty_redeemed)

    @api.depends('qty_deposited', 'qty_remaining', 'unit_price')
    def _compute_amounts(self):
        # M2 fix: 使用 float_round 避免浮點精度誤差
        for line in self:
            line.amount_deposited = float_round(
                line.qty_deposited * line.unit_price, precision_digits=2)
            line.amount_remaining = float_round(
                line.qty_remaining * line.unit_price, precision_digits=2)

    @api.depends('qty_remaining', 'is_cancelled')
    def _compute_state(self):
        for line in self:
            if line.is_cancelled:
                line.state = 'cancelled'
            elif line.qty_remaining <= 0:
                line.state = 'depleted'
            else:
                line.state = 'active'

    def _write_accumulate(self, vals):
        """M1 fix: 內部累加專用方法，繞過 write 保護。
        僅供 consign_add_line 呼叫，不接受外部 context 注入。
        """
        return super().write(vals)

    def write(self, vals):
        """已建立的寄品明細核心欄位不可修改。

        扣除數量請用「核銷」功能，新增品項請用「加入資料行」。
        允許修改的：is_cancelled, storage_note, lot_id, reserved_qty（非核心欄位）。
        """
        protected = {'qty_deposited', 'product_id', 'unit_price', 'product_desc'}
        changed_protected = protected & set(vals.keys())
        if changed_protected:
            for line in self:
                if not isinstance(line.id, models.NewId):
                    raise ValidationError(
                        '已建立的寄品明細不可修改（%s）。\n'
                        '如需扣除請使用「核銷」功能，如需新增請用「加入資料行」。'
                        % ', '.join(changed_protected)
                    )
        return super().write(vals)

    def unlink(self):
        """已建立的寄品明細不可刪除，請使用取消功能。"""
        for line in self:
            if line.qty_redeemed > 0:
                raise ValidationError(
                    '已有核銷紀錄的寄品明細不可刪除（%s）。' % (
                        line.product_desc or line.product_id.name))
        return super().unlink()

    def action_cancel(self):
        for line in self:
            if line.qty_redeemed > 0:
                raise ValidationError(
                    '已有核銷紀錄的品項不可取消（%s，已核銷 %s）。'
                    % (line.product_desc or line.product_id.name, line.qty_redeemed)
                )
        self.write({'is_cancelled': True})
