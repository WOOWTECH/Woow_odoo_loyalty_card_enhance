from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError


class ConsignCartController(http.Controller):
    """Authenticated browser adapter; all ledger decisions remain server-side."""

    @http.route('/shop/consign/allocation', type='json', auth='user', website=True)
    def consign_allocation(self, card_id, product_id, quantity, **kwargs):
        order = request.website.sale_get_order()
        if not order or order.partner_id != request.env.user.partner_id:
            raise AccessError('The current cart is not owned by this user.')
        return order._set_website_consign_allocation(card_id, product_id, quantity)
