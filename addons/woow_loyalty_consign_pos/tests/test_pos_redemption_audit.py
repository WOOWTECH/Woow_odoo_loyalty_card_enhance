# -*- coding: utf-8 -*-

"""Exercise the current POS card lookup and persisted-line contract."""

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('-standard', 'consign_audit')
class TestPosConsignRedemptionAudit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'POS Consign Owner'})
        cls.other_partner = cls.env['res.partner'].create({'name': 'POS Other Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'POS Audit Treatment', 'type': 'service', 'available_in_pos': True,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'POS Audit Consign Program', 'program_type': 'consign',
        })
        cls.card = cls.env['loyalty.card'].with_context(loyalty_no_mail=True).create({
            'program_id': cls.program.id, 'partner_id': cls.partner.id, 'points': 0,
        })
        cls.consign_line = cls.env['loyalty.consign.line'].create({
            'card_id': cls.card.id, 'product_id': cls.product.id,
            'product_desc': cls.product.name, 'qty_deposited': 10.0,
            'unit_price': 100.0,
        })
    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].new({
            'name': 'POS Audit Config', 'enable_consign_redemption': True,
            'company_id': self.env.company.id,
        })

    def _order(self, **line_overrides):
        values = {
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'qty': 2.0,
            'price_unit': 0.0,
            'consign_card_id': self.card.id,
            'consign_covered_qty': 2.0,
            'is_consign_redemption': True,
        }
        values.update(line_overrides)
        return self.env['pos.order'].new({
            'partner_id': self.partner.id,
            'currency_id': self.env.company.currency_id.id,
            'lines': [(0, 0, values)],
        })

    def test_owner_card_lookup_returns_active_balance(self):
        result = self.pos_config.use_consign_card_code(self.card.code, self.partner.id)
        self.assertTrue(result['successful'], result)
        self.assertEqual(result['payload']['card_id'], self.card.id)
        self.assertEqual(result['payload']['lines'][0]['qty_available'], 10.0)

    def test_wrong_customer_lookup_is_rejected(self):
        result = self.pos_config.use_consign_card_code(
            self.card.code, self.other_partner.id,
        )
        self.assertFalse(result['successful'])

    def test_barcode_without_customer_returns_owner_for_auto_selection(self):
        result = self.pos_config.use_consign_card_code(self.card.code, False)
        self.assertTrue(result['successful'])
        self.assertEqual(result['payload']['partner_id'], self.partner.id)

    def test_lookup_requires_pos_feature_flag(self):
        disabled = self.env['pos.config'].new({
            'name': 'Disabled POS', 'enable_consign_redemption': False,
        })
        self.assertFalse(disabled.use_consign_card_code(self.card.code, self.partner.id)['successful'])

    def test_inactive_card_is_not_usable(self):
        self.card.active = False
        self.assertFalse(
            self.pos_config.use_consign_card_code(self.card.code, self.partner.id)['successful']
        )

    def test_persisted_line_is_engine_authority(self):
        order = self._order()
        self.assertEqual(order._consign_requests_from_persisted_lines(), [{
            'card_id': self.card.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'quantity': 2.0,
        }])

    def test_declared_coverage_cannot_disagree_with_line_quantity(self):
        with self.assertRaises(ValidationError):
            self._order(consign_covered_qty=3.0)._consign_requests_from_persisted_lines()

    def test_zero_price_and_redemption_marker_are_required(self):
        for overrides in ({'price_unit': 100.0}, {'is_consign_redemption': False}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self._order(**overrides)._consign_requests_from_persisted_lines()
