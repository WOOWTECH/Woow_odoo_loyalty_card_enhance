from odoo.tests.common import TransactionCase, new_test_user


class TestConsignPortalSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env['res.partner'].create({'name': 'Consign Owner'})
        cls.other = cls.env['res.partner'].create({'name': 'Other Owner'})
        cls.portal = new_test_user(cls.env, login='consign.portal.owner', groups='base.group_portal')
        cls.portal.partner_id = cls.owner
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Portal Consign', 'program_type': 'consign', 'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.product = cls.env['product.product'].create({'name': 'Portal Product', 'type': 'service'})
        engine = cls.env['loyalty.consign.engine']
        cls.own_operation = engine._issue(source=cls.owner, partner=cls.owner, program=cls.program,
            grants=[{'product': cls.product, 'quantity': 2}], idempotency_key='test:portal:own')
        cls.other_operation = engine._issue(source=cls.other, partner=cls.other, program=cls.program,
            grants=[{'product': cls.product, 'quantity': 2}], idempotency_key='test:portal:other')
        cls.own_card = cls.env['loyalty.card'].browse(cls.own_operation.result_json['card_id'])
        cls.other_card = cls.env['loyalty.card'].browse(cls.other_operation.result_json['card_id'])

    def test_portal_effective_rules_prevent_direct_cross_owner_enumeration(self):
        portal_env = self.env(user=self.portal)
        self.assertEqual(portal_env['loyalty.card'].search([('id', '=', self.other_card.id)]), self.env['loyalty.card'])
        self.assertEqual(portal_env['loyalty.consign.line'].search([('card_id', '=', self.other_card.id)]), self.env['loyalty.consign.line'])
        self.assertEqual(portal_env['loyalty.consign.movement'].search([('card_id', '=', self.other_card.id)]), self.env['loyalty.consign.movement'])
        self.assertEqual(portal_env['loyalty.card'].search([('id', '=', self.own_card.id)]), self.own_card)

    def test_portal_acl_is_read_only(self):
        portal_env = self.env(user=self.portal)
        self.assertFalse(portal_env['loyalty.consign.line'].check_access_rights('write', raise_exception=False))
        self.assertFalse(portal_env['loyalty.consign.redemption'].check_access_rights('create', raise_exception=False))
        self.assertFalse(portal_env['loyalty.consign.movement'].check_access_rights('unlink', raise_exception=False))
