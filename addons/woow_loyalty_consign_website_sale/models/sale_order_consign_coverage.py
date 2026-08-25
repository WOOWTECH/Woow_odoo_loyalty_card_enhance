from odoo import fields, models

class SaleOrderConsignCoverage(models.Model):
    _name = 'sale.order.consign.coverage'
    _description = 'Website Consignment Coverage (server projection)'
    _order = 'order_line_id, id'
    allocation_id = fields.Many2one('sale.order.consign.allocation', required=True, ondelete='cascade', check_company=True, index=True)
    order_id = fields.Many2one(related='allocation_id.order_id', store=True, index=True)
    order_line_id = fields.Many2one('sale.order.line', required=True, ondelete='cascade', check_company=True)
    covered_qty = fields.Float(required=True)
    price_tax_fingerprint = fields.Char(required=True)
    reward_line_id = fields.Many2one('sale.order.line', ondelete='set null', check_company=True)
    version = fields.Integer(required=True, index=True)
