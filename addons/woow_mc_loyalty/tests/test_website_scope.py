from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestLoyaltyWebsiteScope(HttpCase):

    def test_second_website_lists_only_its_company_cards(self):
        if not self.env['ir.module.module'].search([
            ('name', '=', 'website'), ('state', '=', 'installed'),
        ], limit=1):
            self.skipTest('The website module is required for a two-website route test')

        first_company = self.env.company
        second_company = self.env['res.company'].create({
            'name': 'Loyalty second website company',
        })
        self.env['website'].create({
            'name': 'Loyalty second website',
            'domain': 'http://loyalty-second.example.test',
            'company_id': second_company.id,
        })
        portal = new_test_user(
            self.env, login='loyalty.website.scope.portal',
            groups='base.group_portal',
        )
        portal.password = 'loyalty-website-test-password'
        portal.company_id = first_company
        portal.company_ids = [(6, 0, [first_company.id])]

        for company, name in (
            (first_company, 'FIRST-WEBSITE-ONLY'),
            (second_company, 'SECOND-WEBSITE-ONLY'),
        ):
            program = self.env['loyalty.program'].create({
                'name': name, 'program_type': 'loyalty',
                'company_id': company.id,
            })
            self.env['loyalty.card'].create({
                'program_id': program.id,
                'partner_id': portal.partner_id.id,
                'points': 10,
            })

        self.authenticate(portal.login, 'loyalty-website-test-password')
        response = self.url_open(
            '/my/member-center/loyalty',
            headers={'Host': 'loyalty-second.example.test'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('SECOND-WEBSITE-ONLY', response.text)
        self.assertNotIn('FIRST-WEBSITE-ONLY', response.text)
