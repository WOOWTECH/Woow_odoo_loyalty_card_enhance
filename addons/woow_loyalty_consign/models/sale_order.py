from odoo import api, fields, models
from odoo.tools import float_compare


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
        """Exclude consign programs from sale_loyalty's automatic program
        management.  Consign cards are handled entirely by
        ``_action_create_consign_card`` and must not be touched by the
        native ``_update_programs_and_rewards`` cleanup logic which would
        otherwise delete zero-point nominative cards created by a
        previous order.
        """
        domain = super()._get_program_domain()
        domain.append(('program_type', '!=', 'consign'))
        return domain

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            order._action_create_consign_card()
        return res

    def _lock_consign_card_partner(self):
        """Serialize automatic card lookup/create even when no card exists."""
        self.ensure_one()
        # This intentional no-op tuple update is stronger than SELECT FOR
        # UPDATE under Odoo's repeatable-read transactions. A concurrent stale
        # snapshot receives SerializationFailure, so the request retry starts
        # with a fresh snapshot before looking up or creating the card.
        self.env.cr.execute(
            'UPDATE res_partner '
            'SET write_date = write_date '
            'WHERE id = %s '
            'RETURNING id',
            (self.partner_id.id,),
        )

    def _action_create_consign_card(self):
        """Interim Task 4 adapter: issue at confirmation until Task 12."""
        self.ensure_one()
        if not self.partner_id:
            return
        programs = self.env['loyalty.program'].search([
            ('program_type', '=', 'consign'),
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('consign_grant_rule_ids', '!=', False),
        ], order='id')
        engine = self.env['loyalty.consign.engine']
        for program in programs:
            grants = []
            trigger_lines = self.env['sale.order.line']
            for rule in program.consign_grant_rule_ids.sorted('id'):
                matching_lines = self.order_line.filtered(
                    lambda line, trigger=rule.trigger_product_id: line.product_id == trigger
                ).sorted(lambda line: (line.sequence, line.id))
                for sale_line in matching_lines:
                    trigger_quantity = sale_line.product_uom._compute_quantity(
                        sale_line.product_uom_qty,
                        rule.trigger_product_id.uom_id,
                        round=False,
                    )
                    if float_compare(
                        trigger_quantity, 0.0,
                        precision_rounding=rule.trigger_product_id.uom_id.rounding,
                    ) <= 0:
                        continue
                    trigger_lines |= sale_line
                    for grant_line in rule.grant_line_ids.sorted('id'):
                        product = grant_line.entitlement_product_id
                        quantity = grant_line.product_uom_id._compute_quantity(
                            grant_line.quantity * trigger_quantity,
                            product.uom_id,
                            round=False,
                        )
                        if float_compare(
                            quantity, 0.0,
                            precision_rounding=product.uom_id.rounding,
                        ) > 0:
                            grants.append({
                                'product': product,
                                'product_uom': product.uom_id,
                                'quantity': quantity,
                                'source_line': sale_line,
                                'source_channel': 'sale',
                                'provenance_key': f'grant-line:{grant_line.id}',
                                'product_desc': product.display_name,
                            })
            if not grants:
                continue
            engine._issue(
                source=self,
                partner=self.partner_id,
                program=program,
                grants=grants,
                idempotency_key=f'consign:sale-order-program:v2:{self.id}:{program.id}',
            )
            # Compatibility marker only; it is never consulted as command
            # authority. Reinvocation always reaches the engine replay path.
            trigger_lines.write({'is_consigned': True})
            self.invalidate_recordset([
                'consign_line_ids', 'consign_line_count',
                'consign_source_movement_ids', 'consign_source_movement_count',
            ])

    def action_view_consign_lines(self):
        """開啟此訂單產生的寄品明細。"""
        self.ensure_one()
        lines = self.consign_line_ids
        return {
            'name': '寄品明細',
            'type': 'ir.actions.act_window',
            'res_model': 'loyalty.consign.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', lines.ids)],
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

    is_consigned = fields.Boolean(
        string='已寄品', default=False, copy=False,
    )
