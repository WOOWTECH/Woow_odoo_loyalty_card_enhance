from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    consign_line_count = fields.Integer(
        string='寄品筆數', compute='_compute_consign_line_count',
    )

    @api.depends('order_line.is_consigned')
    def _compute_consign_line_count(self):
        for order in self:
            order.consign_line_count = len(
                order.order_line.filtered('is_consigned')
            )

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            order._action_create_consign_card()
        return res

    def _action_create_consign_card(self):
        """偵測觸發商品並自動建立/累加寄品卡。"""
        self.ensure_one()
        consign_programs = self.env['loyalty.program'].search([
            ('program_type', '=', 'consign'),
            ('active', '=', True),
        ])
        if not consign_programs:
            return

        for program in consign_programs:
            trigger_products = program.trigger_product_ids
            if not trigger_products:
                continue

            # 訂單行中是否包含此方案的觸發商品
            trigger_lines = self.order_line.filtered(
                lambda l, tp=trigger_products: (
                    l.product_id in tp and not l.is_consigned
                )
            )
            if not trigger_lines:
                continue

            # 找既有卡片或建新卡（一客一卡）
            card = self.env['loyalty.card'].search([
                ('program_id', '=', program.id),
                ('partner_id', '=', self.partner_id.id),
                ('is_consign', '=', True),
                ('active', '=', True),
            ], limit=1)

            if not card:
                card = self.env['loyalty.card'].with_context(
                    loyalty_no_mail=True,
                ).create({
                    'program_id': program.id,
                    'partner_id': self.partner_id.id,
                    'points': 0,
                })

            # 標記觸發商品行（觸發商品本身不存入寄品明細）
            trigger_lines.write({'is_consigned': True})

            # 其他非觸發商品行存入寄品明細
            other_lines = self.order_line.filtered(
                lambda l, tp=trigger_products: (
                    not l.is_consigned
                    and l.product_id
                    and l.product_id not in tp
                )
            )
            for sol in other_lines:
                card.consign_add_line(
                    product=sol.product_id,
                    qty=sol.product_uom_qty,
                    unit_price=sol.price_unit,
                    product_desc=sol.name,
                    sale_line=sol,
                )
                sol.is_consigned = True

            # 發送通知
            card._send_creation_communication(force_send=True)

    def action_view_consign_lines(self):
        """開啟此訂單產生的寄品明細。"""
        self.ensure_one()
        lines = self.env['loyalty.consign.line'].search([
            ('sale_order_id', '=', self.id),
        ])
        return {
            'name': '寄品明細',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', lines.ids)],
            'context': {'create': False},
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    is_consigned = fields.Boolean(
        string='已寄品', default=False, copy=False,
    )
