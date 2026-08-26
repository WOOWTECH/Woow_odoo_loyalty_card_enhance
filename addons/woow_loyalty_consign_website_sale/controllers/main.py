from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.website_sale.controllers.payment import (
    PaymentPortal as WebsiteSalePaymentPortal,
)


class ConsignWebsiteSale(WebsiteSale):
    """Render checkout from the same server-priced coverage snapshot."""

    @http.route()
    def shop_payment(self, **post):
        order = request.website.sale_get_order()
        if order and order.consign_allocation_ids:
            order._recompute_consign_coverage()
        return super().shop_payment(**post)


class ConsignPaymentPortal(WebsiteSalePaymentPortal):
    """Authorize exact cart intent at Odoo's locked payment seam."""

    def _validate_transaction_for_order(self, transaction, sale_order):
        result = super()._validate_transaction_for_order(
            transaction, sale_order,
        )
        if not sale_order.consign_allocation_ids:
            return result
        if (
            request.env.user._is_public()
            or sale_order.partner_id != request.env.user.partner_id
        ):
            raise AccessError(
                'Only the authenticated cart owner may pay with a consignment balance.'
            )
        sale_order._prepare_website_consign_authorization()
        if sale_order.currency_id.compare_amounts(
            transaction.amount, sale_order.amount_total,
        ):
            raise ValidationError(
                'The consignment coverage changed the payable amount. Refresh the checkout.'
            )
        transaction._bind_website_consign_hold()
        if not transaction.consign_hold_id:
            raise ValidationError(
                'The checkout consignment authorization could not be bound to this payment.'
            )
        return result


class ConsignCartController(http.Controller):
    """Authenticated browser adapter; all ledger decisions remain server-side."""

    @http.route('/shop/consign/allocation', type='json', auth='user', website=True)
    def consign_allocation(self, card_id, product_id, quantity, **kwargs):
        order = request.website.sale_get_order()
        if not order or order.partner_id != request.env.user.partner_id:
            raise AccessError('The current cart is not owned by this user.')
        return order._set_website_consign_allocation(card_id, product_id, quantity)

    @http.route('/shop/consign/prepare', type='json', auth='user', website=True)
    def consign_prepare_checkout(self, **kwargs):
        order = request.website.sale_get_order()
        if not order or order.partner_id != request.env.user.partner_id:
            raise AccessError('The current cart is not owned by this user.')
        operation = order._prepare_website_consign_authorization()
        return {'operation_id': operation.id if operation else False,
                'hold_id': operation.hold_ids.id if operation else False,
                'version': order.consign_allocation_version}
