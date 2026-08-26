import inspect
from pathlib import Path

from odoo.tests.common import TransactionCase

from odoo.addons.woow_loyalty_consign_pos.models.pos_order import PosOrder


class TestConsignPosInstall(TransactionCase):

    def test_legacy_core_pos_acls_are_replaced_by_bridge_acls(self):
        acl_xmlid_names = {
            'access_loyalty_consign_line_pos_user',
            'access_loyalty_consign_redemption_pos_user',
            'access_loyalty_consign_redemption_line_pos_user',
        }
        xmlid_model = self.env['ir.model.data'].sudo()
        legacy_xmlids = xmlid_model.search([
            ('module', '=', 'woow_loyalty_consign'),
            ('name', 'in', list(acl_xmlid_names)),
            ('model', '=', 'ir.model.access'),
        ])
        bridge_xmlids = xmlid_model.search([
            ('module', '=', 'woow_loyalty_consign_pos'),
            ('name', 'in', list(acl_xmlid_names)),
            ('model', '=', 'ir.model.access'),
        ])

        self.assertFalse(legacy_xmlids)
        self.assertSetEqual(set(bridge_xmlids.mapped('name')), acl_xmlid_names)

        bridge_acls = self.env['ir.model.access'].sudo().browse(
            bridge_xmlids.mapped('res_id'),
        ).exists()
        pos_group = self.env.ref('point_of_sale.group_pos_user')
        model_names = {
            'loyalty.consign.line',
            'loyalty.consign.redemption',
            'loyalty.consign.redemption.line',
        }
        consign_models = self.env['ir.model'].sudo().search([
            ('model', 'in', list(model_names)),
        ])
        matching_acls = self.env['ir.model.access'].sudo().search([
            ('group_id', '=', pos_group.id),
            ('model_id', 'in', consign_models.ids),
        ])

        self.assertEqual(len(bridge_acls), 3)
        self.assertSetEqual(set(consign_models.mapped('model')), model_names)
        self.assertSetEqual(set(matching_acls.ids), set(bridge_acls.ids))
        expected_permissions = {
            'loyalty.consign.line': (True, False, False, False),
            # Task 7 removes POS generic document creation.  Task 15 will
            # restore only a server-side online adapter, never POS CRUD.
            'loyalty.consign.redemption': (True, False, False, False),
            'loyalty.consign.redemption.line': (True, False, False, False),
        }
        for access in bridge_acls:
            with self.subTest(model=access.model_id.model):
                self.assertEqual(
                    (
                        access.perm_read,
                        access.perm_write,
                        access.perm_create,
                        access.perm_unlink,
                    ),
                    expected_permissions[access.model_id.model],
                )

    def test_pos_install_routes_only_through_private_lifecycle_commands(self):
        engine = self.env['loyalty.consign.engine']
        for method in ('_authorize', '_capture', '_release'):
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(engine, method, None)))
        source = inspect.getsource(PosOrder)
        self.assertIn("self.env['loyalty.consign.engine']", source)
        self.assertIn('_authorize(', source)
        self.assertIn('_capture(', source)
        self.assertIn('_release(', source)
        self.assertNotIn('loyalty.consign.redemption', source)
        self.assertNotIn('_append_movement(', source)

    def test_frontend_uses_persisted_line_intent_without_post_payment_rpc(self):
        module_root = Path(__file__).resolve().parents[1]
        product_source = (module_root / 'static/src/overrides/components/product_screen/product_screen.js').read_text()
        popup_source = (module_root / 'static/src/overrides/components/consign_card_popup/consign_card_popup.js').read_text()
        payment_source = (module_root / 'static/src/overrides/components/payment_screen/payment_screen.js').read_text()
        order_source = (module_root / 'static/src/overrides/models/pos_order.js').read_text()
        self.assertIn('consign_card_id', product_source)
        self.assertIn('consign_covered_qty', product_source)
        self.assertNotIn('consign_line_id:', product_source)
        self.assertNotIn('confirm_consign_redemptions', payment_source)
        self.assertNotIn('consignRedemptions', order_source)
        self.assertIn('navigator.onLine', order_source)
        self.assertIn('syncAllOrders({ orders: [order], throw: true })', order_source)
        self.assertNotIn('parseInt', popup_source)
        self.assertIn('uom_rounding', popup_source)

    def test_backend_order_fields_do_not_replace_frontend_order_schema(self):
        loaded_fields = self.env['pos.order']._load_pos_data_fields(False)
        for backend_only_field in (
            'consign_hold_id',
            'consign_authorize_operation_id',
            'consign_capture_operation_id',
            'consign_allocation_hash',
            'consign_state',
        ):
            self.assertNotIn(backend_only_field, loaded_fields)

    def test_pos_consign_feature_is_disabled_by_default(self):
        self.assertFalse(
            self.env['pos.config'].new({}).enable_consign_redemption
        )
        # Odoo 18 deliberately returns an empty field list for pos.config;
        # search_read then loads every readable field, including our flag.
        self.assertNotIn(
            'enable_consign_redemption',
            self.env['pos.config']._load_pos_data_fields(False),
        )

    def test_redemption_product_reuses_legacy_xmlid(self):
        redemption_product = self.env.ref(
            'woow_loyalty_consign.consign_redemption_product',
        )
        old_duplicate = self.env.ref(
            'woow_loyalty_consign_pos.consign_pos_redemption_product',
            raise_if_not_found=False,
        )

        self.assertEqual(redemption_product._name, 'product.product')
        self.assertTrue(redemption_product.active)
        self.assertTrue(redemption_product.available_in_pos)
        self.assertFalse(redemption_product.sale_ok)
        if old_duplicate and old_duplicate != redemption_product:
            self.assertFalse(old_duplicate.active)
        self.assertEqual(
            self.env['pos.config']._get_consign_redemption_product_id(),
            redemption_product.id,
        )
