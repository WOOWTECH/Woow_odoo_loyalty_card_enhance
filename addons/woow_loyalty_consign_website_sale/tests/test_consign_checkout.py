from datetime import timedelta
from uuid import uuid4

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestConsignWebsiteCheckout(TransactionCase):
    """Payment callbacks may only transition the Hold they snapshotted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Website checkout owner'})
        cls.product = cls.env['product.product'].create({
            'name': 'Website checkout product', 'type': 'service', 'list_price': 100,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Website checkout program', 'program_type': 'consign',
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.provider = cls.env['payment.provider'].search([('code', '=', 'demo')], limit=1)

    def _order_with_hold(self, quantity=1):
        issue = self.env['loyalty.consign.engine']._issue(
            self.partner, self.partner, self.program,
            [{'product': self.product, 'quantity': quantity}],
            'test:website:issue:%s' % uuid4(),
        )
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_uom_qty': quantity,
                'product_uom': self.product.uom_id.id, 'price_unit': 100,
            })],
        })
        self.env['sale.order.consign.allocation'].create({
            'order_id': order.id, 'card_id': issue.movement_ids.card_id.id,
            'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
            'requested_qty': quantity, 'version': order.consign_allocation_version,
        })
        operation = order._prepare_website_consign_authorization()
        return order, operation.hold_ids

    def _transaction(self, order):
        if not self.provider:
            self.skipTest('The payment_demo provider is required for checkout tests.')
        return self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'reference': 'TEST-WEBSITE-CONSIGN-%s' % uuid4(),
            'amount': max(order.amount_total, 0.01),
            'currency_id': order.currency_id.id,
            'partner_id': order.partner_id.id,
            'sale_order_ids': [(6, 0, order.ids)],
        })

    def test_done_capture_is_exact_replay(self):
        order, hold = self._order_with_hold()
        tx = self._transaction(order)
        tx.write({'state': 'done'})
        tx._post_process()
        operation = order.consign_capture_operation_id
        self.assertEqual(hold.state, 'captured')
        self.assertTrue(operation)
        movement_count = self.env['loyalty.consign.movement'].search_count([
            ('operation_id', '=', operation.id),
        ])
        tx._post_process()
        self.assertEqual(order.consign_capture_operation_id, operation)
        self.assertEqual(self.env['loyalty.consign.movement'].search_count([
            ('operation_id', '=', operation.id),
        ]), movement_count)

    def test_authorized_transaction_never_captures(self):
        order, hold = self._order_with_hold()
        tx = self._transaction(order)
        tx.write({'state': 'authorized'})
        tx._post_process()
        self.assertEqual(hold.state, 'active')
        self.assertFalse(order.consign_capture_operation_id)

    def test_error_and_cancel_release_only_snapshotted_hold(self):
        for state in ('error', 'cancel'):
            order, hold = self._order_with_hold()
            tx = self._transaction(order)
            tx.write({'state': state})
            tx._post_process()
            self.assertEqual(hold.state, 'released')

    def test_stale_callback_cannot_capture_or_release_newer_hold(self):
        order, old_hold = self._order_with_hold()
        old_tx = self._transaction(order)
        order._invalidate_consign_allocations()
        self.assertEqual(old_hold.state, 'released')
        # Re-create the same intent for a newer cart version.
        self.env['sale.order.consign.allocation'].create({
            'order_id': order.id, 'card_id': old_hold.card_id.id,
            'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
            'requested_qty': 1, 'version': order.consign_allocation_version,
        })
        order._prepare_website_consign_authorization()
        new_hold = order.consign_hold_operation_id.hold_ids
        old_tx.write({'state': 'error'})
        old_tx._post_process()
        self.assertEqual(new_hold.state, 'active')
        old_tx.write({'state': 'done'})
        with self.assertRaises(ValidationError):
            old_tx._post_process()
        self.assertEqual(new_hold.state, 'active')

    def test_expired_hold_cannot_be_late_captured(self):
        order, hold = self._order_with_hold()
        tx = self._transaction(order)
        hold.write({'expires_at': fields.Datetime.now() - timedelta(minutes=1)})
        self.env['loyalty.consign.engine']._expire_holds(limit=10)
        tx.write({'state': 'done'})
        with self.assertRaises(ValidationError):
            tx._post_process()
        self.assertEqual(hold.state, 'expired')
        self.assertFalse(order.consign_capture_operation_id)

    def test_competing_transaction_is_fenced(self):
        order, hold = self._order_with_hold()
        winner, loser = self._transaction(order), self._transaction(order)
        winner.write({'state': 'done'})
        winner._post_process()
        loser.write({'state': 'done'})
        with self.assertRaises(ValidationError):
            loser._post_process()
        self.assertEqual(hold.state, 'captured')
        self.assertEqual(order.consign_payment_transaction_id, winner)

    def test_zero_total_neither_authorizes_nor_captures(self):
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.assertFalse(order._prepare_website_consign_authorization())
        self.assertFalse(order.consign_hold_operation_id)
        self.assertFalse(order.consign_capture_operation_id)
