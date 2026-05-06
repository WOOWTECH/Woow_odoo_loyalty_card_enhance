from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class LoyaltyCard(models.Model):
    # 原生 loyalty.card 已繼承 mail.thread，此處只需追加 activity 和 portal mixin
    _inherit = ['loyalty.card', 'mail.activity.mixin', 'portal.mixin']
    _name = 'loyalty.card'
    _description = 'Loyalty Card'

    is_consign = fields.Boolean(
        string='是寄品卡', compute='_compute_is_consign', store=True,
    )
    consign_card_type = fields.Selection(
        related='program_id.consign_card_type', store=True, string='寄品卡類型',
    )
    consign_line_ids = fields.One2many(
        'loyalty.consign.line', 'card_id', string='寄品明細', copy=False,
    )
    consign_redemption_ids = fields.One2many(
        'loyalty.consign.redemption', 'card_id', string='核銷紀錄', copy=False,
    )
    consign_total_remaining_qty = fields.Float(
        string='剩餘總數量', compute='_compute_consign_totals', store=True,
    )
    consign_total_remaining_value = fields.Monetary(
        string='剩餘總金額', compute='_compute_consign_totals', store=True,
        currency_field='currency_id',
    )
    consign_active_lines = fields.Integer(
        string='有效品項數', compute='_compute_consign_totals', store=True,
    )
    consign_expiry_date = fields.Date(
        string='卡片到期日', compute='_compute_consign_expiry_date',
    )

    @api.depends('program_id.program_type')
    def _compute_is_consign(self):
        for card in self:
            card.is_consign = card.program_id.program_type == 'consign'

    @api.depends(
        'consign_line_ids.qty_remaining',
        'consign_line_ids.amount_remaining',
        'consign_line_ids.state',
    )
    def _compute_consign_totals(self):
        for card in self:
            active_lines = card.consign_line_ids.filtered(
                lambda l: l.state == 'active'
            )
            card.consign_total_remaining_qty = sum(active_lines.mapped('qty_remaining'))
            card.consign_total_remaining_value = sum(active_lines.mapped('amount_remaining'))
            card.consign_active_lines = len(active_lines)

    def _compute_access_url(self):
        super()._compute_access_url()
        for card in self:
            if card.is_consign:
                card.access_url = f'/my/consign-cards/{card.id}'

    @api.depends('program_id.consign_expiry_months')
    def _compute_consign_expiry_date(self):
        for card in self:
            months = card.program_id.consign_expiry_months
            if card.is_consign and months and card.create_date:
                card.consign_expiry_date = card.create_date.date() + relativedelta(months=months)
            else:
                card.consign_expiry_date = False

    def consign_add_line(self, product, qty, unit_price, product_desc=None, sale_line=None):
        """新增或累加寄品明細至此卡片。

        若方案啟用 consign_one_card_per_partner 且同品項已存在有效行，
        則累加數量；否則建立新行。

        Args:
            product: product.product recordset 或 ID (int)
        """
        self.ensure_one()
        if isinstance(product, int):
            product = self.env['product.product'].browse(product)
        program = self.program_id
        expiry_date = False
        if program.consign_expiry_months:
            expiry_date = fields.Date.context_today(self) + relativedelta(
                months=program.consign_expiry_months
            )

        existing = False
        if program.consign_one_card_per_partner:
            existing = self.consign_line_ids.filtered(
                lambda l: l.product_id == product
                and l.state == 'active'
                and l.unit_price == unit_price
            )[:1]

        if existing:
            existing.qty_deposited += qty
        else:
            vals = {
                'card_id': self.id,
                'product_id': product.id,
                'product_desc': product_desc or product.display_name,
                'qty_deposited': qty,
                'unit_price': unit_price,
                'date_deposited': fields.Date.context_today(self),
                'date_expiry': expiry_date,
            }
            if sale_line:
                vals['sale_line_id'] = sale_line.id if hasattr(sale_line, 'id') else sale_line
            self.env['loyalty.consign.line'].create(vals)

    def action_send_consign_card(self):
        """手動重寄寄品卡通知。"""
        self.ensure_one()
        template = self.program_id.consign_mail_template_id
        if template:
            template.send_mail(self.id, force_send=True)

    def action_open_redeem_wizard(self):
        """開啟核銷精靈。"""
        self.ensure_one()
        return {
            'name': '寄品核銷',
            'type': 'ir.actions.act_window',
            'res_model': 'consign.redeem.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_card_id': self.id,
            },
        }

    def _send_creation_communication(self, force_send=False):
        """寄品卡走寄品專用範本，其他走原生流程。"""
        consign_cards = self.filtered('is_consign')
        other_cards = self - consign_cards
        if other_cards:
            super(LoyaltyCard, other_cards)._send_creation_communication(
                force_send=force_send
            )
        for card in consign_cards:
            if self.env.context.get('loyalty_no_mail') or \
               self.env.context.get('action_no_send_mail'):
                continue
            template = card.program_id.consign_mail_template_id
            if not template or not card.partner_id:
                continue
            if card.program_id.consign_send_mail:
                template.send_mail(
                    res_id=card.id,
                    force_send=force_send,
                    email_layout_xmlid='mail.mail_notification_light',
                )
