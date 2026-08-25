import inspect

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

    def test_pos_consign_feature_is_disabled_by_default(self):
        self.assertFalse(
            self.env['pos.config'].new({}).enable_consign_redemption
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
