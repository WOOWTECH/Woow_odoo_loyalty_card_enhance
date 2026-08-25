from odoo import api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyCard(models.Model):
    # 原生 loyalty.card 已繼承 mail.thread，此處只需追加 activity 和 portal mixin
    _inherit = ['loyalty.card', 'mail.activity.mixin', 'portal.mixin']
    _name = 'loyalty.card'
    _description = 'Loyalty Card'

    is_consign = fields.Boolean(
        string='是寄品卡', compute='_compute_is_consign', store=True,
    )
    consign_line_ids = fields.One2many(
        'loyalty.consign.line', 'card_id', string='寄品明細', copy=False,
    )
    consign_redemption_ids = fields.One2many(
        'loyalty.consign.redemption', 'card_id', string='核銷紀錄', copy=False,
    )
    consign_movement_ids = fields.One2many(
        'loyalty.consign.movement', 'card_id', string='Ledger Movements', copy=False,
    )
    consign_movement_count = fields.Integer(
        string='Movement Count', compute='_compute_consign_movement_count',
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

    def write(self, vals):
        if vals.get('active') is False:
            consign_cards = self.filtered('is_consign')
            if consign_cards:
                # Match the command hierarchy: lock cards before checking the
                # active Hold rows which prevent a safe direct deactivation.
                for card_id in sorted(consign_cards.ids):
                    self.env.cr.execute(
                        '''UPDATE loyalty_card SET write_date = write_date
                            WHERE id = %s RETURNING id''',
                        (card_id,),
                    )
                    if not self.env.cr.fetchone():
                        raise ValidationError('The consignment card no longer exists.')
                self.env.cr.execute(
                    '''SELECT hold.id
                         FROM loyalty_consign_hold hold
                        WHERE hold.state = 'active'
                          AND EXISTS (
                              SELECT 1
                                FROM loyalty_consign_hold_allocation allocation
                                JOIN loyalty_consign_line line
                                  ON line.id = allocation.aggregate_line_id
                               WHERE allocation.hold_id = hold.id
                                 AND line.card_id = ANY(%s)
                          )
                     ORDER BY hold.id
                        FOR UPDATE''',
                    (consign_cards.ids,),
                )
                if self.env.cr.fetchall():
                    raise ValidationError(
                        'A consignment card with an active Hold cannot be deactivated.'
                    )
        return super().write(vals)

    @api.depends('consign_movement_ids')
    def _compute_consign_movement_count(self):
        for card in self:
            card.consign_movement_count = len(card.consign_movement_ids)

    @api.depends('program_id.program_type')
    def _compute_is_consign(self):
        for card in self:
            card.is_consign = card.program_id.program_type == 'consign'

    @api.depends(
        'consign_line_ids.qty_available',
        'consign_line_ids.qty_remaining',
        'consign_line_ids.amount_remaining',
        'consign_line_ids.state',
    )
    def _compute_consign_totals(self):
        for card in self:
            active_lines = card.consign_line_ids.filtered(
                lambda line: line.state == 'active' and line.qty_available > 0
            )
            card.consign_total_remaining_qty = sum(active_lines.mapped('qty_available'))
            # Historical issue unit_price is audit-only. Derive the available
            # compatibility value from the ledger's net remaining value so a
            # value-preserving reversal cannot leave card totals inconsistent.
            card.consign_total_remaining_value = sum(
                line.amount_remaining * line.qty_available / line.qty_remaining
                for line in active_lines if line.qty_remaining
            )
            card.consign_active_lines = len(active_lines)

    def _compute_access_url(self):
        super()._compute_access_url()
        for card in self:
            if card.is_consign:
                card.access_url = f'/my/consign-cards/{card.id}'

    def _consign_add_line(self, product, qty, unit_price, product_desc=None, sale_line=None):
        """新增或累加寄品明細至此卡片。

        一客一卡制：同來源訂單行、同品項同價格的 active line 才累加，
        不同來源必須保留各自的寄品明細。

        Args:
            product: product.product recordset 或 ID (int)
        """
        self.ensure_one()
        if isinstance(product, int):
            product = self.env['product.product'].browse(product)

        sale_line_id = (
            sale_line.id if hasattr(sale_line, 'id') else sale_line
        ) if sale_line else False

        # Task 4 has exactly one projection per card/product/UoM. Source and
        # grant provenance live on independent immutable issue movements.
        vals = {
            'card_id': self.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'product_desc': product_desc or product.display_name,
            'qty_deposited': qty,
            'unit_price': unit_price,
            'date_deposited': fields.Date.context_today(self),
        }
        if sale_line_id:
            vals['sale_line_id'] = sale_line_id
        if sale_line_id:
            # The sale adapter appends one richer issue movement per resolved
            # grant row immediately after preserving this legacy projection.
            return self.env['loyalty.consign.line']._create_for_specific_movement(vals)
        return self.env['loyalty.consign.line'].create(vals)

    def action_view_consign_movements(self):
        self.ensure_one()
        return {
            'name': 'Consignment Movements',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.movement',
            'view_mode': 'list,form',
            'domain': [('card_id', '=', self.id)],
            'context': {'create': False, 'delete': False},
        }

    def action_send_consign_card(self):
        """手動重寄寄品卡通知。"""
        self.ensure_one()
        template = self.program_id.mail_template_id
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
            template = card.program_id.mail_template_id
            if not template or not card.partner_id:
                continue
            template.send_mail(
                res_id=card.id,
                force_send=force_send,
                email_layout_xmlid='mail.mail_notification_light',
            )
