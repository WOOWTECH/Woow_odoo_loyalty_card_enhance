# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('-standard', 'consign_audit')
class TestConsignRedemptionAudit(TransactionCase):
    """Executable audit of current ledger, wizard, and Sales behavior.

    Tests named ``invariant`` describe behavior required before website/POS
    redemption can safely share this ledger. They intentionally fail against
    the current implementation when the invariant is not enforced.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Consign Audit Customer',
            'email': 'consign-audit@example.com',
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Other Consign Customer',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Audit Treatment',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.trigger_product = cls.env['product.product'].create({
            'name': 'Audit Package Trigger',
            'type': 'service',
            'list_price': 1000.0,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Audit Consign Program',
            'program_type': 'consign',
            'active': True,
            'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': cls.trigger_product.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': cls.product.id,
                    'product_uom_id': cls.product.uom_id.id,
                    'quantity': 2.0,
                })],
            })],
        })
        cls.card = cls.env['loyalty.card'].with_context(
            loyalty_no_mail=True,
        ).create({
            'program_id': cls.program.id,
            'partner_id': cls.partner.id,
            'points': 0,
        })
        cls.consign_line = cls.env['loyalty.consign.line'].create({
            'card_id': cls.card.id,
            'product_id': cls.product.id,
            'product_desc': cls.product.name,
            'qty_deposited': 10.0,
            'unit_price': 100.0,
        })

    def _redemption(self, quantities, card=None, consign_line=None):
        card = card or self.card
        consign_line = consign_line or self.consign_line
        return self.env['loyalty.consign.redemption'].create({
            'card_id': card.id,
            'service_note': 'Automated redemption audit',
            'line_ids': [
                (0, 0, {
                    'consign_line_id': consign_line.id,
                    'qty_redeemed': qty,
                })
                for qty in quantities
            ],
        })

    def _sale_order(self, products, partner=None):
        partner = partner or self.partner
        return self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': product.id,
                    'name': product.name,
                    'product_uom_qty': qty,
                    'product_uom': product.uom_id.id,
                    'price_unit': product.list_price,
                })
                for product, qty in products
            ],
        })

    # Known-good control cases.

    def test_control_happy_redemption_updates_balance(self):
        redemption = self._redemption([2.0])
        redemption.action_done()

        self.assertEqual(redemption.state, 'done')
        self.assertEqual(self.consign_line.qty_redeemed, 2.0)
        self.assertEqual(self.consign_line.qty_remaining, 8.0)

    def test_control_over_redemption_is_rejected(self):
        redemption = self._redemption([11.0])

        with self.assertRaises(ValidationError):
            redemption.action_done()
        self.assertEqual(redemption.state, 'draft')
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_control_manual_wizard_redeems_selected_quantity(self):
        wizard = self.env['consign.redeem.wizard'].create({
            'card_id': self.card.id,
            'line_ids': [(0, 0, {
                'consign_line_id': self.consign_line.id,
                'selected': True,
                'qty_to_redeem': 3.0,
            })],
        })

        action = wizard.action_confirm()
        redemption = self.env['loyalty.consign.redemption'].browse(action['res_id'])
        self.assertEqual(redemption.state, 'done')
        self.assertEqual(self.consign_line.qty_remaining, 7.0)

    # Required shared-ledger invariants. These expose current defects.

    def test_invariant_freshly_created_line_can_be_redeemed_immediately(self):
        fresh_line = self.env['loyalty.consign.line'].create({
            'card_id': self.card.id,
            'product_id': self.product.id,
            'qty_deposited': 1.0,
            'unit_price': 100.0,
        })
        redemption = self._redemption([1.0], consign_line=fresh_line)

        redemption.action_done()
        self.assertEqual(redemption.state, 'done')
        self.assertEqual(fresh_line.qty_redeemed, 1.0)

    def test_invariant_duplicate_source_lines_cannot_over_redeem(self):
        redemption = self._redemption([6.0, 6.0])

        with self.assertRaises(ValidationError):
            redemption.action_done()
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_invariant_repeated_wizard_submission_is_idempotent(self):
        wizard = self.env['consign.redeem.wizard'].create({
            'card_id': self.card.id,
            'line_ids': [(0, 0, {
                'consign_line_id': self.consign_line.id,
                'selected': True,
                'qty_to_redeem': 2.0,
            })],
        })

        first = wizard.action_confirm()
        second = wizard.action_confirm()
        self.assertEqual(first['res_id'], second['res_id'])
        self.assertEqual(self.consign_line.qty_redeemed, 2.0)

    def test_invariant_done_redemption_line_is_immutable(self):
        redemption = self._redemption([2.0])
        redemption.action_done()

        with self.assertRaises(ValidationError):
            redemption.line_ids.write({'qty_redeemed': 8.0})
        self.assertEqual(self.consign_line.qty_redeemed, 2.0)

    def test_invariant_done_redemption_cannot_be_deleted(self):
        redemption = self._redemption([2.0])
        redemption.action_done()

        with self.assertRaises(ValidationError):
            redemption.unlink()
        self.assertTrue(redemption.exists())

    def test_invariant_inactive_card_cannot_redeem(self):
        self.card.active = False
        redemption = self._redemption([1.0])

        with self.assertRaises(ValidationError):
            redemption.action_done()
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_invariant_non_consign_card_cannot_hold_or_redeem_lines(self):
        normal_program = self.env['loyalty.program'].create({
            'name': 'Normal Audit Loyalty',
            'program_type': 'loyalty',
        })
        normal_card = self.env['loyalty.card'].create({
            'program_id': normal_program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })

        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.line'].create({
                'card_id': normal_card.id,
                'product_id': self.product.id,
                'qty_deposited': 1.0,
                'unit_price': 100.0,
            })

    def test_invariant_default_email_template_renders_with_lines(self):
        template = self.env.ref('woow_loyalty_consign.mail_template_consign_card')

        rendered = template._render_field('body_html', [self.card.id])
        self.assertIn(self.product.name, rendered[self.card.id])

    def test_security_portal_reads_only_own_consign_records(self):
        portal = new_test_user(
            self.env,
            login='consign_audit_portal',
            groups='base.group_portal',
        )
        own_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': portal.partner_id.id,
            'points': 0,
        })
        own_line = self.env['loyalty.consign.line'].create({
            'card_id': own_card.id,
            'product_id': self.product.id,
            'qty_deposited': 1.0,
            'unit_price': 100.0,
        })
        own_redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': own_card.id,
            'line_ids': [(0, 0, {
                'consign_line_id': own_line.id,
                'qty_redeemed': 1.0,
            })],
        })
        portal_env = self.env(user=portal)

        visible_cards = portal_env['loyalty.card'].search([
            ('is_consign', '=', True),
        ])
        visible_lines = portal_env['loyalty.consign.line'].search([])
        visible_redemptions = portal_env['loyalty.consign.redemption'].search([])
        self.assertIn(own_card, visible_cards)
        self.assertNotIn(self.card, visible_cards)
        self.assertEqual(visible_lines, own_line)
        self.assertEqual(visible_redemptions, own_redemption)

    # Sales issuance controls and lifecycle observations.

    def test_invariant_consign_program_can_persist_trigger_products(self):
        trigger = self.env['product.product'].create({
            'name': 'Explicit Trigger Rule Audit',
            'type': 'service',
        })
        program = self.env['loyalty.program'].create({
            'name': 'Explicit Trigger Rule Program',
            'program_type': 'consign',
            'active': True,
            'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': self.product.id,
                    'product_uom_id': self.product.uom_id.id,
                    'quantity': 1.0,
                })],
            })],
        })

        self.assertIn(trigger, program.consign_trigger_product_ids)

    def test_control_sale_confirmation_issues_one_card_and_deposit(self):
        sale_partner = self.env['res.partner'].create({
            'name': 'Fresh Sale Consign Customer',
        })
        order = self._sale_order([
            (self.trigger_product, 1.0),
            (self.product, 5.0),
        ], partner=sale_partner)
        order.action_confirm()

        cards = self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', sale_partner.id),
        ])
        issued_lines = cards.consign_line_ids.filtered(
            lambda line: line.sale_order_id == order
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(sum(issued_lines.mapped('qty_deposited')), 2.0)
        self.assertNotIn(self.trigger_product, issued_lines.product_id)

    def test_observation_cancelled_sale_leaves_issued_balance_active(self):
        sale_partner = self.env['res.partner'].create({
            'name': 'Cancelled Sale Consign Customer',
        })
        order = self._sale_order([
            (self.trigger_product, 1.0),
            (self.product, 2.0),
        ], partner=sale_partner)
        order.action_confirm()
        issued_line = self.env['loyalty.consign.line'].search([
            ('sale_order_id', '=', order.id),
        ], limit=1)

        order._action_cancel()

        self.assertEqual(order.state, 'cancel')
        self.assertEqual(issued_line.state, 'active')
        self.assertEqual(issued_line.qty_remaining, 2.0)

    def test_invariant_merged_deposit_retains_sale_provenance(self):
        order = self._sale_order([
            (self.trigger_product, 1.0),
            (self.product, 2.0),
        ])
        order.action_confirm()

        issued_lines = self.env['loyalty.consign.line'].search([
            ('sale_order_id', '=', order.id),
        ])
        self.assertEqual(sum(issued_lines.mapped('qty_deposited')), 2.0)

    def test_invariant_each_triggered_program_receives_explicit_allocation(self):
        second_trigger = self.env['product.product'].create({
            'name': 'Second Audit Trigger',
            'type': 'service',
        })
        second_entitlement = self.env['product.product'].create({
            'name': 'Second Audit Entitlement',
            'type': 'service',
        })
        second_program = self.env['loyalty.program'].create({
            'name': 'Second Audit Consign Program',
            'program_type': 'consign',
            'active': True,
            'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': second_trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': second_entitlement.id,
                    'product_uom_id': second_entitlement.uom_id.id,
                    'quantity': 3.0,
                })],
            })],
        })
        order = self._sale_order([
            (self.trigger_product, 1.0),
            (second_trigger, 1.0),
            (self.product, 2.0),
        ])
        order.action_confirm()

        first_card = self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', self.partner.id),
        ], limit=1)
        second_card = self.env['loyalty.card'].search([
            ('program_id', '=', second_program.id),
            ('partner_id', '=', self.partner.id),
        ], limit=1)
        self.assertEqual(first_card.consign_line_ids.product_id, self.product)
        self.assertEqual(second_card.consign_line_ids.product_id, second_entitlement)
        issued_products = (
            first_card.consign_line_ids.product_id
            | second_card.consign_line_ids.product_id
        )
        self.assertFalse(issued_products & (self.trigger_product | second_trigger))
