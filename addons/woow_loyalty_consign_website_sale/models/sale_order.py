from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

class SaleOrderConsignAllocation(models.Model):
    _name = 'sale.order.consign.allocation'
    _description = 'Website Consignment Allocation Intent'
    _order = 'id'
    _sql_constraints = [('allocation_identity', 'unique(order_id, card_id, product_id, product_uom_id)', 'A card may select a product only once per order and UoM.')]
    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', check_company=True, index=True)
    card_id = fields.Many2one('loyalty.card', required=True, check_company=True, index=True)
    product_id = fields.Many2one('product.product', required=True, check_company=True, index=True)
    product_uom_id = fields.Many2one('uom.uom', required=True)
    requested_qty = fields.Float(required=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    version = fields.Integer(required=True, readonly=True)
    coverage_ids = fields.One2many('sale.order.consign.coverage', 'allocation_id', readonly=True)

    @api.constrains('order_id', 'card_id', 'product_id', 'product_uom_id', 'requested_qty')
    def _check_intent(self):
        for rec in self:
            if rec.requested_qty <= 0 or rec.product_uom_id != rec.product_id.uom_id:
                raise ValidationError(_('The selected product quantity must be positive in its base UoM.'))
            if rec.card_id.company_id != rec.order_id.company_id or rec.product_id.company_id not in (False, rec.order_id.company_id):
                raise ValidationError(_('Card, product, and order must belong to one company.'))
            if rec.card_id.partner_id != rec.order_id.partner_id:
                raise ValidationError(_('The selected card does not belong to the order customer.'))

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    consign_allocation_version = fields.Integer(default=0, readonly=True, copy=False)
    consign_allocation_ids = fields.One2many('sale.order.consign.allocation', 'order_id', readonly=True)
    consign_allocation_warning = fields.Json(readonly=True, copy=False)
    consign_hold_operation_id = fields.Many2one('loyalty.consign.operation', readonly=True, copy=False)
    consign_hold_version = fields.Integer(readonly=True, copy=False)
    consign_payment_transaction_id = fields.Many2one('payment.transaction', readonly=True, copy=False)
    consign_capture_operation_id = fields.Many2one('loyalty.consign.operation', readonly=True, copy=False)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    consign_generated_reward = fields.Boolean(default=False, readonly=True, copy=False)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _consign_eligible_lines(self, product):
        return self.order_line.filtered(lambda l: l.product_id == product and not l.display_type and not l.is_reward_line and not l.consign_generated_reward).sorted(lambda l: (l.sequence, l.id))

    def _revalidate_consign_allocations(self):
        for order in self:
            warnings = []
            for allocation in order.consign_allocation_ids:
                maximum = sum(order._consign_eligible_lines(allocation.product_id).mapped('product_uom_qty'))
                if allocation.requested_qty > maximum:
                    allocation.sudo().write({'requested_qty': maximum})
                    warnings.append({'allocation_id': allocation.id, 'requested_qty': maximum, 'reason': 'cart_quantity_reduced'})
            order.consign_allocation_warning = warnings
            order._recompute_consign_coverage()
        return self.consign_allocation_warning

    def _recompute_consign_coverage(self):
        """Server-priced, deterministic coverage; browser input has no price seam."""
        Coverage = self.env['sale.order.consign.coverage'].sudo()
        for order in self:
            old = Coverage.search([('order_id', '=', order.id)])
            old.mapped('reward_line_id').sudo().unlink()
            old.unlink()
            for allocation in order.consign_allocation_ids:
                remaining = allocation.requested_qty
                for line in order._consign_eligible_lines(allocation.product_id):
                    if remaining <= 0:
                        break
                    already = sum(Coverage.search([('order_line_id', '=', line.id)]).mapped('covered_qty'))
                    quantity = min(remaining, max(0, line.product_uom_qty - already))
                    if quantity <= 0:
                        continue
                    fingerprint = '%s|%s|%s' % (line.price_unit, line.discount, tuple(line.tax_id.ids))
                    reward = self.env['sale.order.line'].sudo().create({
                        'order_id': order.id, 'product_id': line.product_id.id,
                        'product_uom_qty': quantity, 'product_uom': line.product_uom.id,
                        'price_unit': -line.price_unit, 'discount': line.discount,
                        'tax_id': [(6, 0, line.tax_id.ids)], 'is_reward_line': True,
                        'consign_generated_reward': True,
                        'name': _('Consignment redemption: %s') % line.name,
                    })
                    Coverage.create({'allocation_id': allocation.id, 'order_line_id': line.id,
                                     'covered_qty': quantity, 'price_tax_fingerprint': fingerprint,
                                     'reward_line_id': reward.id, 'version': order.consign_allocation_version})
                    remaining -= quantity

    def _lock_website_consign_payment(self):
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', (self.id,))

    def _prepare_website_consign_authorization(self):
        """Checkout-only trusted seam; payment callbacks capture in the next phase."""
        self.ensure_one()
        if not self.consign_allocation_ids:
            return False
        self._recompute_consign_coverage()
        requests = [{'card_id': row.card_id.id, 'product_id': row.product_id.id,
                     'product_uom_id': row.product_uom_id.id, 'quantity': row.requested_qty}
                    for row in self.consign_allocation_ids]
        operation = self.env['loyalty.consign.engine']._authorize(
            self, self.partner_id, requests,
            'website-authorize-%s-%s' % (self.id, self.consign_allocation_version),
        )
        self.sudo().write({
            'consign_hold_operation_id': operation.id,
            'consign_hold_version': self.consign_allocation_version,
            'consign_payment_transaction_id': False,
            'consign_capture_operation_id': False,
        })
        return operation

    def _release_website_consign_holds(self):
        """Trusted cart lifecycle seam; never exposed through RPC."""
        self.ensure_one()
        holds = self.env['loyalty.consign.hold'].sudo().search([
            ('state', '=', 'active'), ('source_model', '=', self._name),
            ('source_res_id', '=', self.id),
        ])
        engine = self.env['loyalty.consign.engine']
        for hold in holds:
            engine._release(self, self.partner_id, hold, 'website-cart-release-%s-%s' % (self.id, hold.id))

    def _invalidate_consign_allocations(self):
        self.ensure_one()
        self._release_website_consign_holds()
        self.sudo().write({'consign_allocation_version': self.consign_allocation_version + 1})
        coverage = self.env['sale.order.consign.coverage'].sudo().search([('order_id', '=', self.id)])
        coverage.mapped('reward_line_id').sudo().unlink()
        coverage.unlink()
        return self._revalidate_consign_allocations()

    def _set_website_consign_allocation(self, card_id, product_id, quantity):
        self.ensure_one()
        if not self.website_id.consign_redemption_enabled:
            raise ValidationError(_('Consignment redemption is not enabled for this website.'))
        card = self.env['loyalty.card'].browse(card_id).exists()
        product = self.env['product.product'].browse(product_id).exists()
        if not card or not product or self.partner_id != self.env.user.partner_id:
            raise ValidationError(_('The requested card or product is unavailable.'))
        if card.partner_id != self.partner_id or card.company_id != self.company_id or (product.company_id and product.company_id != self.company_id):
            raise ValidationError(_('The selected card, product, and cart must belong to one customer and company.'))
        if not self._consign_eligible_lines(product) or quantity <= 0:
            raise ValidationError(_('The selected product is not eligible in this cart.'))
        self._invalidate_consign_allocations()
        allocation = self.env['sale.order.consign.allocation'].sudo().search([('order_id','=',self.id),('card_id','=',card.id),('product_id','=',product.id),('product_uom_id','=',product.uom_id.id)], limit=1)
        vals = {'requested_qty': quantity, 'version': self.consign_allocation_version}
        if allocation:
            allocation.write(vals)
        else:
            allocation = self.env['sale.order.consign.allocation'].sudo().create(dict(vals, order_id=self.id, card_id=card.id, product_id=product.id, product_uom_id=product.uom_id.id))
        return {'allocation_id': allocation.id, 'version': self.consign_allocation_version, 'warnings': self._revalidate_consign_allocations()}

    def _cart_update(self, *args, **kwargs):
        result = super()._cart_update(*args, **kwargs)
        self._invalidate_consign_allocations()
        return result
