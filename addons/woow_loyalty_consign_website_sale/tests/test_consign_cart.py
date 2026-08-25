from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestConsignCart(TransactionCase):
    def test_feature_defaults_off_and_card_owner_is_required(self):
        website = self.env['website'].get_current_website()
        self.assertFalse(website.consign_redemption_enabled)
        owner = self.env['res.partner'].create({
            'name': 'Website Cart Card Owner', 'company_id': self.env.company.id,
        })
        other_partner = self.env['res.partner'].create({
            'name': 'Website Cart Other Customer', 'company_id': self.env.company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Website Cart Product', 'type': 'service',
        })
        program = self.env['loyalty.program'].create({
            'name': 'Website Cart Program', 'program_type': 'consign',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        issue = self.env['loyalty.consign.engine']._issue(
            source=owner, partner=owner, program=program,
            grants=[{'product': product, 'quantity': 1}],
            idempotency_key='test:website:cart-owner',
        )
        order = self.env['sale.order'].create({'partner_id': other_partner.id})
        card = issue.movement_ids.card_id
        with self.assertRaises(ValidationError):
            self.env['sale.order.consign.allocation'].create({
                'order_id': order.id,
                'card_id': card.id,
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'requested_qty': 1,
                'version': order.consign_allocation_version,
            })
