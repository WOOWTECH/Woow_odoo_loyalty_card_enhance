from odoo import api, fields, models
from odoo.exceptions import ValidationError


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
            line.qty_remaining = line.qty_deposited - line.qty_redeemed

    @api.depends('qty_deposited', 'qty_remaining', 'unit_price')
    def _compute_amounts(self):
        for line in self:
            line.amount_deposited = line.qty_deposited * line.unit_price
            line.amount_remaining = line.qty_remaining * line.unit_price

    @api.depends('qty_remaining', 'is_cancelled')
    def _compute_state(self):
        for line in self:
            if line.is_cancelled:
                line.state = 'cancelled'
            elif line.qty_remaining <= 0:
                line.state = 'depleted'
            else:
                line.state = 'active'

    def action_cancel(self):
        self.write({'is_cancelled': True})
