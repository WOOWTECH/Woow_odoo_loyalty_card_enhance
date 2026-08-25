from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError

class TestConsignCart(TransactionCase):
    def test_feature_defaults_off_and_owner_is_required(self):
        website = self.env['website'].get_current_website()
        self.assertFalse(website.consign_redemption_enabled)
        order = self.env['sale.order'].create({'partner_id': self.env.ref('base.partner_demo').id})
        card = self.env['loyalty.card'].search([], limit=1)
        if card:
            with self.assertRaises(ValidationError):
                self.env['sale.order.consign.allocation'].create({'order_id': order.id, 'card_id': card.id, 'product_id': self.env.ref('product.product_product_4').id, 'product_uom_id': self.env.ref('uom.product_uom_unit').id, 'requested_qty': 1, 'version': 0})
