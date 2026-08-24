from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class TestConsignInstall(TransactionCase):

    def test_core_models_are_registered(self):
        for model_name in (
            'loyalty.consign.grant.rule',
            'loyalty.consign.grant.line',
            'loyalty.consign.operation',
            'loyalty.consign.movement',
            'loyalty.consign.hold',
            'loyalty.consign.hold.allocation',
            'loyalty.consign.refund.saga',
            'loyalty.consign.line',
            'loyalty.consign.redemption',
            'loyalty.consign.redemption.line',
            'consign.redeem.wizard',
            'consign.redeem.wizard.line',
        ):
            with self.subTest(model=model_name):
                self.assertIn(model_name, self.env.registry.models)

    def test_consign_card_form_defines_movement_button_box(self):
        view = self.env.ref(
            'woow_loyalty_consign.loyalty_card_consign_view_form',
        )
        arch = etree.fromstring(view.arch_db.encode())

        button_boxes = arch.xpath(
            "./xpath[@expr='//sheet']/div[@name='button_box']",
        )
        self.assertEqual(len(button_boxes), 1)
        self.assertTrue(button_boxes[0].xpath(
            ".//button[@name='action_view_consign_movements']",
        ))

    def test_core_does_not_depend_on_point_of_sale(self):
        consign_module = self.env['ir.module.module'].search([
            ('name', '=', 'woow_loyalty_consign'),
        ])

        self.assertTrue(consign_module)
        self.assertNotIn(
            'point_of_sale', consign_module.dependencies_id.mapped('name'),
        )

    def test_movement_trigger_and_schema_indexes_exist(self):
        self.env.cr.execute(
            """
            SELECT COUNT(*)
              FROM pg_trigger
             WHERE tgname = 'woow_loyalty_consign_movement_immutable_trg'
               AND tgrelid = 'loyalty_consign_movement'::regclass
               AND NOT tgisinternal
            """
        )
        self.assertEqual(self.env.cr.fetchone()[0], 1)
        self.env.cr.execute(
            """
            SELECT COUNT(*)
              FROM pg_indexes
             WHERE tablename IN (
                 'loyalty_consign_operation',
                 'loyalty_consign_movement',
                 'loyalty_consign_hold',
                 'loyalty_consign_hold_allocation',
                 'loyalty_consign_refund_saga'
             )
            """
        )
        self.assertGreater(self.env.cr.fetchone()[0], 5)

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
