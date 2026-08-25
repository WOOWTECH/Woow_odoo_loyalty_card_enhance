from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    consign_line_ids = fields.Many2many(
        'loyalty.consign.line', compute='_compute_consign_source_movements',
        string='寄品明細',
    )
    consign_line_count = fields.Integer(
        string='寄品筆數', compute='_compute_consign_source_movements',
    )
    consign_source_movement_ids = fields.Many2many(
        'loyalty.consign.movement', compute='_compute_consign_source_movements',
        string='Source Movements',
    )
    consign_source_movement_count = fields.Integer(
        compute='_compute_consign_source_movements', string='Movement Count',
    )

    @api.depends('order_line')
    def _compute_consign_source_movements(self):
        movement_model = self.env['loyalty.consign.movement']
        for order in self:
            movements = movement_model.search([
                ('source_model', '=', 'sale.order.line'),
                ('source_res_id', 'in', order.order_line.ids),
            ]) if order.order_line else movement_model.browse()
            order.consign_source_movement_ids = movements
            order.consign_source_movement_count = len(movements)
            order.consign_line_ids = movements.mapped('aggregate_line_id')
            order.consign_line_count = len(order.consign_line_ids)

    def _get_program_domain(self):
        """Keep consign programs out of sale_loyalty's reward-card flow.

        Cards are created only by the paid-invoice adapter in account.move.
        """
        domain = super()._get_program_domain()
        domain.append(('program_type', '!=', 'consign'))
        return domain

    def action_view_consign_lines(self):
        self.ensure_one()
        return {
            'name': '寄品明細',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.consign_line_ids.ids)],
            'context': {'create': False},
        }

    def action_view_consign_movements(self):
        self.ensure_one()
        return {
            'name': 'Consignment Movements',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.movement',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.consign_source_movement_ids.ids)],
            'context': {'create': False, 'delete': False},
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # Kept solely to read old database rows.  It is no longer written by the
    # consignment adapter and is not authority for entitlement issuance.
    is_consigned = fields.Boolean(string='已寄品', readonly=True, copy=False)
