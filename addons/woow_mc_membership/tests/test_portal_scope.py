from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.addons.woow_mc_membership.controllers.portal import MembershipPortal


@tagged('post_install', '-at_install')
class TestMembershipPortalScope(TransactionCase):

    def test_undated_line_does_not_make_a_member_current(self):
        partner = self.env['res.partner'].create({'name': 'Undated member'})
        product = self.env['product.product'].create({'name': 'Undated membership'})
        line = self.env['membership.membership_line'].create({
            'partner': partner.id, 'membership_id': product.id,
            'member_price': 100,
        })
        self.assertEqual(MembershipPortal()._own_membership_state(partner, line), 'none')

    def test_free_member_remains_free_with_an_unpaid_line(self):
        partner = self.env['res.partner'].create({
            'name': 'Free member', 'free_member': True,
        })
        product = self.env['product.product'].create({'name': 'Free membership'})
        line = self.env['membership.membership_line'].create({
            'partner': partner.id, 'membership_id': product.id,
            'member_price': 100,
        })
        self.assertEqual(MembershipPortal()._own_membership_state(partner, line), 'free')

    def test_portal_cannot_read_another_company_membership_line(self):
        company = self.env.company
        other_company = self.env['res.company'].create({'name': 'Membership other company'})
        portal = new_test_user(
            self.env, login='membership.scope.portal', groups='base.group_portal',
        )
        portal.company_id = company
        portal.company_ids = [(6, 0, [company.id])]
        product = self.env['product.product'].create({'name': 'Membership test'})
        journal = self.env['account.journal'].search([
            ('company_id', '=', other_company.id), ('type', '=', 'sale'),
        ], limit=1)
        if not journal:
            journal = self.env['account.journal'].with_company(other_company).create({
                'name': 'Other Company Sales', 'code': 'OCS', 'type': 'sale',
                'company_id': other_company.id,
            })
        account = self.env['account.account'].create({
            'name': 'Membership Revenue', 'code': 'MC4001',
            'account_type': 'income',
            'company_ids': [(6, 0, [other_company.id])],
        })
        receivable = self.env['account.account'].create({
            'name': 'Membership Receivable', 'code': 'MC1101',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_ids': [(6, 0, [other_company.id])],
        })
        portal.partner_id.with_company(other_company).property_account_receivable_id = receivable
        invoice = self.env['account.move'].with_company(other_company).create({
            'move_type': 'out_invoice',
            'company_id': other_company.id,
            'partner_id': portal.partner_id.id,
            'journal_id': journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Membership test',
                'product_id': product.id,
                'account_id': account.id,
                'quantity': 1,
                'price_unit': 100,
            })],
        })
        line = self.env['membership.membership_line'].create({
            'partner': portal.partner_id.id,
            'membership_id': product.id,
            'member_price': 100,
            'account_invoice_line': invoice.invoice_line_ids[0].id,
        })
        self.assertEqual(line.company_id, other_company)
        visible = self.env['membership.membership_line'].with_user(portal).search([])
        self.assertNotIn(line.id, visible.ids)
