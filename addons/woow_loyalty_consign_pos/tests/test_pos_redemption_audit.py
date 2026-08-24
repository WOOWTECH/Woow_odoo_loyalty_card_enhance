# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('-standard', 'consign_audit')
class TestPosConsignRedemptionAudit(TransactionCase):
    """Executable audit of current POS redemption backend contracts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'POS Consign Owner',
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'POS Other Customer',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'POS Audit Treatment',
            'type': 'service',
            'available_in_pos': True,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'POS Audit Consign Program',
            'program_type': 'consign',
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
        # Current backend methods do not use persisted config/order fields;
        # ``new`` isolates their actual RPC contract without unrelated POS setup.
        cls.pos_config = cls.env['pos.config'].new({'name': 'POS Audit Config'})

    def _payload(self, qty=2.0):
        return {
            'card_id': self.card.id,
            'lines': [{
                'consign_line_id': self.consign_line.id,
                'qty_redeemed': qty,
                'note': 'POS audit payload',
            }],
        }

    def _order(self, partner=None, env=None):
        env = env or self.env
        return env['pos.order'].new({
            'name': 'POS/AUDIT/0001',
            'partner_id': partner.id if partner else False,
        })

    # Known-good lookup controls.

    def test_control_owner_card_lookup_returns_active_balance(self):
        result = self.pos_config.use_consign_card_code(
            self.card.code,
            self.partner.id,
        )

        self.assertTrue(result['successful'])
        self.assertEqual(result['payload']['card_id'], self.card.id)
        self.assertEqual(result['payload']['lines'][0]['qty_available'], 10.0)

    def test_control_wrong_customer_card_lookup_is_rejected(self):
        result = self.pos_config.use_consign_card_code(
            self.card.code,
            self.other_partner.id,
        )

        self.assertFalse(result['successful'])

    # Required POS contracts. These expose current defects.

    def test_invariant_barcode_without_customer_can_resolve_owner_for_auto_set(self):
        result = self.pos_config.use_consign_card_code(self.card.code, False)

        self.assertTrue(result['successful'])
        self.assertEqual(result['payload']['partner_id'], self.partner.id)

    def test_invariant_final_order_customer_must_own_card(self):
        order = self._order(partner=self.other_partner)

        with self.assertRaises(ValidationError):
            order.confirm_consign_redemptions(self._payload())
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_invariant_pos_order_lines_are_authoritative(self):
        order = self._order(partner=self.partner)
        self.assertFalse(order.lines)

        with self.assertRaises(ValidationError):
            order.confirm_consign_redemptions(self._payload())
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_invariant_repeated_callback_is_idempotent(self):
        order = self._order(partner=self.partner)
        first = order.confirm_consign_redemptions(self._payload())
        second = order.confirm_consign_redemptions(self._payload())

        self.assertTrue(first['successful'])
        self.assertTrue(second['successful'])
        redemptions = self.env['loyalty.consign.redemption'].search([
            ('service_note', '=', 'POS Redemption - POS/AUDIT/0001'),
        ])
        self.assertEqual(len(redemptions), 1)
        self.assertEqual(self.consign_line.qty_redeemed, 2.0)

    def test_invariant_inactive_card_is_rejected_at_final_confirmation(self):
        self.card.active = False
        order = self._order(partner=self.partner)

        with self.assertRaises(ValidationError):
            order.confirm_consign_redemptions(self._payload())
        self.assertEqual(self.consign_line.qty_redeemed, 0.0)

    def test_invariant_pos_only_cashier_can_complete_supported_flow(self):
        cashier = new_test_user(
            self.env,
            login='pos_consign_audit_cashier',
            groups='base.group_user,point_of_sale.group_pos_user',
        )
        order = self._order(partner=self.partner, env=self.env(user=cashier))

        result = order.confirm_consign_redemptions(self._payload())
        self.assertTrue(result['successful'])
        self.assertEqual(self.consign_line.qty_redeemed, 2.0)
