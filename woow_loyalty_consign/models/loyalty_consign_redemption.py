from odoo import api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyConsignRedemption(models.Model):
    _name = 'loyalty.consign.redemption'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _description = '寄品核銷單'
    _order = 'date_redemption desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='核銷單號', readonly=True, copy=False, default='/',
    )
    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True,
        domain=[('is_consign', '=', True)],
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', store=True, string='客戶',
    )
    date_redemption = fields.Datetime(
        string='核銷日期', default=fields.Datetime.now,
    )
    staff_user_id = fields.Many2one(
        'res.users', string='服務人員', default=lambda self: self.env.user,
    )
    service_note = fields.Text(
        string='服務備註',
        help='施打劑量、操作師、備註等',
    )
    state = fields.Selection(
        [
            ('draft', '草稿'),
            ('done', '已完成'),
        ],
        string='狀態', default='draft', readonly=True, copy=False,
        tracking=True,
    )
    line_ids = fields.One2many(
        'loyalty.consign.redemption.line', 'redemption_id', string='核銷明細',
    )
    total_redeemed_value = fields.Monetary(
        string='核銷總金額', compute='_compute_total_redeemed_value',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='card_id.currency_id', store=True,
    )

    def _compute_access_url(self):
        super()._compute_access_url()
        for rec in self:
            rec.access_url = f'/my/consign-redemptions/{rec.id}'

    @api.depends('line_ids.subtotal')
    def _compute_total_redeemed_value(self):
        for rec in self:
            rec.total_redeemed_value = sum(rec.line_ids.mapped('subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'loyalty.consign.redemption'
                ) or '/'
        return super().create(vals_list)

    def action_done(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError('只有草稿狀態的核銷單才能確認。')
            if not rec.line_ids:
                raise ValidationError('核銷單至少需要一筆明細。')

            # 鎖定相關寄品明細列，防止並行核銷超額 (TOCTOU)
            consign_line_ids = rec.line_ids.mapped('consign_line_id').ids
            if consign_line_ids:
                self.env.cr.execute(
                    "SELECT id FROM loyalty_consign_line WHERE id IN %s FOR UPDATE",
                    [tuple(consign_line_ids)],
                )
                # 鎖定後重新讀取最新數量
                rec.line_ids.mapped('consign_line_id').invalidate_recordset(
                    ['qty_remaining', 'qty_redeemed'],
                )

            for line in rec.line_ids:
                if line.qty_redeemed <= 0:
                    raise ValidationError(
                        f'品項「{line.product_desc or line.product_id.name}」的核銷數量必須大於 0。'
                    )
                if line.qty_redeemed > line.consign_line_id.qty_remaining:
                    raise ValidationError(
                        f'品項「{line.product_desc or line.product_id.name}」'
                        f'核銷數量 ({line.qty_redeemed}) 超過剩餘數量 '
                        f'({line.consign_line_id.qty_remaining})。'
                    )
            rec.state = 'done'
            rec.card_id.message_post(
                body=f'核銷單 {rec.name} 已完成，共核銷 {len(rec.line_ids)} 筆品項。',
                message_type='notification',
            )


class LoyaltyConsignRedemptionLine(models.Model):
    _name = 'loyalty.consign.redemption.line'
    _description = '寄品核銷明細'
    _rec_name = 'product_desc'

    redemption_id = fields.Many2one(
        'loyalty.consign.redemption', string='核銷單', required=True, ondelete='cascade',
    )
    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='寄品明細', required=True,
    )
    product_id = fields.Many2one(
        related='consign_line_id.product_id', store=True, string='品項',
    )
    product_desc = fields.Char(
        related='consign_line_id.product_desc', store=True, string='品項說明',
    )
    qty_available = fields.Float(
        related='consign_line_id.qty_remaining', string='可用數量',
    )
    qty_redeemed = fields.Float(
        string='本次核銷數量',
    )
    unit_price = fields.Float(
        related='consign_line_id.unit_price', store=True, string='單價',
    )
    subtotal = fields.Monetary(
        string='小計', compute='_compute_subtotal', store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='redemption_id.currency_id', store=True,
    )
    note = fields.Char(string='備註')

    @api.depends('qty_redeemed', 'unit_price')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.qty_redeemed * line.unit_price

    @api.constrains('qty_redeemed')
    def _check_qty_redeemed(self):
        for line in self:
            if line.qty_redeemed < 0:
                raise ValidationError('核銷數量不可為負數。')
