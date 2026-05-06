from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    consign_program_id = fields.Many2one(
        'loyalty.program',
        string='寄品方案',
        domain=[('program_type', '=', 'consign')],
    )
    auto_consign = fields.Boolean(
        string='確認時自動建立寄品',
        default=False,
    )
    consign_card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', compute='_compute_consign_card_id',
    )
    consign_line_count = fields.Integer(
        string='寄品筆數', compute='_compute_consign_line_count',
    )

    @api.depends('partner_id', 'consign_program_id')
    def _compute_consign_card_id(self):
        for order in self:
            if order.consign_program_id and order.partner_id:
                order.consign_card_id = self.env['loyalty.card'].search([
                    ('program_id', '=', order.consign_program_id.id),
                    ('partner_id', '=', order.partner_id.id),
                    ('is_consign', '=', True),
                    ('active', '=', True),
                ], limit=1)
            else:
                order.consign_card_id = False

    @api.depends('order_line.is_consigned')
    def _compute_consign_line_count(self):
        for order in self:
            order.consign_line_count = len(
                order.order_line.filtered('is_consigned')
            )

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            if order.auto_consign and order.consign_program_id:
                order._action_create_consign_card()
        return res

    def _action_create_consign_card(self):
        """建立或累加寄品卡。"""
        self.ensure_one()
        program = self.consign_program_id
        card = False

        if program.consign_one_card_per_partner:
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

        for sol in self.order_line.filtered(lambda l: not l.is_consigned and l.product_id):
            card.consign_add_line(
                product=sol.product_id,
                qty=sol.product_uom_qty,
                unit_price=sol.price_unit,
                product_desc=sol.name,
                sale_line=sol,
            )
            sol.is_consigned = True

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
