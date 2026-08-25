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

    def _consign_eligible_lines(self, product):
        return self.order_line.filtered(lambda l: l.product_id == product and not l.display_type and not l.is_reward_line).sorted(lambda l: (l.sequence, l.id))

    def _revalidate_consign_allocations(self):
        for order in self:
            warnings = []
            for allocation in order.consign_allocation_ids:
                maximum = sum(order._consign_eligible_lines(allocation.product_id).mapped('product_uom_qty'))
                if allocation.requested_qty > maximum:
                    allocation.sudo().write({'requested_qty': maximum})
                    warnings.append({'allocation_id': allocation.id, 'requested_qty': maximum, 'reason': 'cart_quantity_reduced'})
            order.consign_allocation_warning = warnings
        return self.consign_allocation_warning

    def _invalidate_consign_allocations(self):
        self.ensure_one()
        self.sudo().write({'consign_allocation_version': self.consign_allocation_version + 1})
        self.env['sale.order.consign.coverage'].sudo().search([('order_id', '=', self.id)]).unlink()
        return self._revalidate_consign_allocations()

    def _cart_update(self, *args, **kwargs):
        result = super()._cart_update(*args, **kwargs)
        self._invalidate_consign_allocations()
        return result
