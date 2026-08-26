from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestConsignCart(TransactionCase):
    def test_feature_defaults_off_and_card_owner_is_required(self):
        # The HAOS Web-visible test website may be explicitly enabled.  Verify
        # the field default on a new website instead of assuming runtime config.
        self.assertFalse(
            self.env['website'].new({}).consign_redemption_enabled
        )
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

    def test_zero_cart_quantity_removes_allocation_instead_of_writing_zero(self):
        owner = self.env['res.partner'].create({
            'name': 'Website Zero Cart Owner',
            'company_id': self.env.company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Website Zero Cart Product',
            'type': 'service',
            'company_id': self.env.company.id,
        })
        program = self.env['loyalty.program'].create({
            'name': 'Website Zero Cart Program',
            'program_type': 'consign',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        issue = self.env['loyalty.consign.engine']._issue(
            source=owner,
            partner=owner,
            program=program,
            grants=[{'product': product, 'quantity': 1}],
            idempotency_key='test:website:zero-cart-issue',
        )
        order = self.env['sale.order'].create({
            'partner_id': owner.id,
            'company_id': self.env.company.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'product_uom': product.uom_id.id,
                'price_unit': 100,
            })],
        })
        allocation = self.env['sale.order.consign.allocation'].create({
            'order_id': order.id,
            'card_id': issue.movement_ids.card_id.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'requested_qty': 1,
            'version': order.consign_allocation_version,
        })
        order._recompute_consign_coverage()
        base_line = order.order_line.filtered(
            lambda line: not line.consign_generated_reward
        )
        self.assertTrue(order.order_line.filtered('consign_generated_reward'))

        order._cart_update(
            product_id=product.id,
            line_id=base_line.id,
            add_qty=-1,
        )

        self.assertFalse(self.env['sale.order.consign.allocation'].search([
            ('id', '=', allocation.id),
        ]))
        self.assertFalse(order.order_line.filtered('consign_generated_reward'))
        self.assertFalse(order.order_line.filtered(
            lambda line: line.product_id == product
        ))
