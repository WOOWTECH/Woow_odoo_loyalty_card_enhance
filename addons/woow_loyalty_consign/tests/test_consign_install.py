from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class TestConsignInstall(TransactionCase):

    def test_core_models_are_registered(self):
        for model_name in (
            'loyalty.consign.grant.rule',
            'loyalty.consign.grant.line',
            'loyalty.consign.line',
            'loyalty.consign.redemption',
            'loyalty.consign.redemption.line',
            'consign.redeem.wizard',
            'consign.redeem.wizard.line',
        ):
            with self.subTest(model=model_name):
                self.assertIn(model_name, self.env.registry.models)

    def test_core_does_not_depend_on_point_of_sale(self):
        consign_module = self.env['ir.module.module'].search([
            ('name', '=', 'woow_loyalty_consign'),
        ])

        self.assertTrue(consign_module)
        self.assertNotIn(
            'point_of_sale', consign_module.dependencies_id.mapped('name'),
        )

    def test_core_creates_generic_redemption_product(self):
        redemption_product = self.env.ref(
            'woow_loyalty_consign.consign_redemption_product',
        )

        self.assertEqual(redemption_product.type, 'service')
        self.assertEqual(redemption_product.list_price, 0)
        self.assertFalse(redemption_product.sale_ok)
        self.assertFalse(redemption_product.purchase_ok)


@tagged('-standard', 'consign_clean_install')
class TestConsignCleanInstall(TransactionCase):
    """Environment assertions run explicitly by the core-only install gate."""

    def test_point_of_sale_is_uninstalled(self):
        pos_module = self.env['ir.module.module'].search([
            ('name', '=', 'point_of_sale'),
        ])

        self.assertTrue(
            pos_module, 'point_of_sale must be available to check its state',
        )
        self.assertEqual(pos_module.state, 'uninstalled')
