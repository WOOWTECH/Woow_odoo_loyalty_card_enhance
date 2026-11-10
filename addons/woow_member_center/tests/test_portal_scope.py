from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestMemberCenterPortalScope(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({
            'name': 'Member Center Other Company',
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Card owner'})
        cls.other_partner = cls.env['res.partner'].create({'name': 'Other card owner'})
        cls.portal = new_test_user(
            cls.env, login='member.center.scope.portal', groups='base.group_portal',
        )
        cls.portal.partner_id = cls.partner
        cls.portal.company_id = cls.company
        cls.portal.company_ids = [(6, 0, [cls.company.id])]

        def card_for(partner, company):
            program = cls.env['loyalty.program'].create({
                'name': 'Scope test', 'program_type': 'loyalty',
                'company_id': company.id,
            })
            card = cls.env['loyalty.card'].create({
                'program_id': program.id, 'partner_id': partner.id,
                'points': 10,
            })
            history = cls.env['loyalty.history'].create({
                'card_id': card.id, 'description': 'Scope test', 'issued': 10,
            })
            return card, history

        cls.own_card, cls.own_history = card_for(cls.partner, cls.company)
        cls.foreign_company_card, cls.foreign_company_history = card_for(
            cls.partner, cls.other_company,
        )
        cls.other_owner_card, cls.other_owner_history = card_for(
            cls.other_partner, cls.company,
        )

    def test_portal_direct_card_search_is_owner_and_company_scoped(self):
        cards = self.env['loyalty.card'].with_user(self.portal).search([])
        self.assertIn(self.own_card.id, cards.ids)
        self.assertNotIn(self.foreign_company_card.id, cards.ids)
        self.assertNotIn(self.other_owner_card.id, cards.ids)

    def test_portal_direct_history_search_is_owner_and_company_scoped(self):
        history = self.env['loyalty.history'].with_user(self.portal).search([])
        self.assertIn(self.own_history.id, history.ids)
        self.assertNotIn(self.foreign_company_history.id, history.ids)
        self.assertNotIn(self.other_owner_history.id, history.ids)
