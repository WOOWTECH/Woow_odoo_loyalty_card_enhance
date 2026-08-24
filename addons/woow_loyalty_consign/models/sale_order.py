from odoo import api, fields, models
from odoo.tools import float_compare


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    consign_line_ids = fields.One2many(
        'loyalty.consign.line',
        'sale_order_id',
        string='寄品明細',
    )
    consign_line_count = fields.Integer(
        string='寄品筆數', compute='_compute_consign_line_count',
    )
    consign_source_movement_ids = fields.Many2many(
        'loyalty.consign.movement', compute='_compute_consign_source_movements',
        string='Source Movements',
    )
    consign_source_movement_count = fields.Integer(
        compute='_compute_consign_source_movements', string='Movement Count',
    )

    @api.depends('consign_line_ids')
    def _compute_consign_line_count(self):
        for order in self:
            order.consign_line_count = len(order.consign_line_ids)

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
        """Issue configured grants for trigger quantities on this order.

        This is an interim confirmation-time adapter. Paid-event issuance will
        replace it later; grant resolution itself is already explicit here.
        """
        self.ensure_one()
        if not self.partner_id:
            return

        consign_programs = self.env['loyalty.program'].search([
            ('program_type', '=', 'consign'),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('consign_grant_rule_ids', '!=', False),
        ], order='id')
        if not consign_programs:
            return

        self._lock_consign_card_partner()
        for program in consign_programs:
            grants = []
            trigger_lines = self.env['sale.order.line']
            for rule in program.consign_grant_rule_ids.sorted('id'):
                matching_lines = self.order_line.filtered(
                    lambda line, trigger=rule.trigger_product_id: (
                        not line.is_consigned
                        and line.product_id == trigger
                    )
                ).sorted(lambda line: (line.sequence, line.id))
                for sale_line in matching_lines:
                    trigger_quantity = sale_line.product_uom._compute_quantity(
                        sale_line.product_uom_qty,
                        rule.trigger_product_id.uom_id,
                        round=False,
                    )
                    if float_compare(
                        trigger_quantity,
                        0.0,
                        precision_rounding=rule.trigger_product_id.uom_id.rounding,
                    ) <= 0:
                        continue
                    trigger_lines |= sale_line
                    for grant_line in rule.grant_line_ids.sorted('id'):
                        quantity = grant_line.product_uom_id._compute_quantity(
                            grant_line.quantity * trigger_quantity,
                            grant_line.entitlement_product_id.uom_id,
                            round=False,
                        )
                        if float_compare(
                            quantity,
                            0.0,
                            precision_rounding=(
                                grant_line.entitlement_product_id.uom_id.rounding
                            ),
                        ) > 0:
                            grants.append((sale_line, grant_line, quantity))

            # Never create a card when no positive configured entitlement was
            # resolved. Unrelated order lines are deliberately ignored.
            if not grants:
                continue

            # Preserve the current row lock while card creation is still owned
            # by this interim adapter.
            self.env.cr.execute(
                "SELECT id FROM loyalty_card "
                "WHERE program_id = %s AND partner_id = %s AND active = TRUE "
                "ORDER BY id FOR UPDATE",
                (program.id, self.partner_id.id),
            )
            locked_ids = [row[0] for row in self.env.cr.fetchall()]
            card = self.env['loyalty.card'].browse(locked_ids).filtered(
                'is_consign'
            )[:1]

            is_new_card = not card
            if is_new_card:
                card = self.env['loyalty.card'].with_context(
                    loyalty_no_mail=True,
                ).create({
                    'program_id': program.id,
                    'partner_id': self.partner_id.id,
                    'points': 0,
                })

            for sale_line, grant_line, quantity in grants:
                product = grant_line.entitlement_product_id
                aggregate_line = card._consign_add_line(
                    product=product,
                    qty=quantity,
                    unit_price=product.list_price,
                    product_desc=product.display_name,
                    sale_line=sale_line,
                )
                self.env['loyalty.consign.movement']._append_movement(
                    aggregate_line=aggregate_line,
                    movement_type='issue',
                    quantity=quantity,
                    source_channel='sale',
                    source_model='sale.order.line',
                    source_res_id=sale_line.id,
                    source_name=sale_line.order_id.display_name,
                    idempotency_key=(
                        f'consign:sale-grant:v1:{sale_line.id}:{grant_line.id}'
                    ),
                    unit_value=product.list_price,
                    product_desc_snapshot=product.display_name,
                )
            trigger_lines.write({'is_consigned': True})

            if is_new_card:
                # Card creation suppresses native loyalty mail only for the
                # create call. Re-browse in the order's original environment
                # before sending the dedicated consignment notification.
                notification_card = self.env['loyalty.card'].browse(card.ids)
                notification_card._send_creation_communication(force_send=True)

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
