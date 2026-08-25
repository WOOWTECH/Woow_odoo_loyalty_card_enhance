from odoo.tests.common import TransactionCase


class TestConsignPaidIssuance(TransactionCase):
    """Paid-invoice adapter regression tests.

    Payment settlement itself belongs to account's test suite; these tests keep
    the consign boundary explicit and exercise the durable adapter command.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Paid Grant Customer'})
        cls.trigger = cls.env['product.product'].create({
            'name': 'Paid Trigger', 'type': 'service', 'list_price': 100,
        })
        cls.entitlement = cls.env['product.product'].create({
            'name': 'Paid Entitlement', 'type': 'service', 'list_price': 0,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Paid Grant Program',
            'program_type': 'consign',
            'company_id': cls.env.company.id,
            'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': cls.trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': cls.entitlement.id,
                    'product_uom_id': cls.entitlement.uom_id.id,
                    'quantity': 2,
                })],
            })],
        })

    def _order(self, quantity=1):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.trigger.id,
                'name': self.trigger.display_name,
                'product_uom_qty': quantity,
                'product_uom': self.trigger.uom_id.id,
                'price_unit': 100,
            })],
        })

    def test_confirmation_never_issues_consign_entitlement(self):
        order = self._order()
        order.action_confirm()
        self.assertFalse(order.consign_source_movement_ids)
        self.assertFalse(self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id), ('partner_id', '=', self.partner.id),
        ]))

    def test_unpaid_invoice_adapter_is_a_noop(self):
        order = self._order()
        order.action_confirm()
        invoice = order._create_invoices()
        invoice._issue_consign_paid_invoice_grants()
        self.assertFalse(order.consign_source_movement_ids)

    def test_paid_invoice_line_command_replays_exactly_once(self):
        order = self._order(2)
        order.action_confirm()
        invoice = order._create_invoices()
        # Account owns this computed state.  The adapter test calls the same
        # durable hook after the account payment tests have transitioned it.
        invoice.with_context(check_move_validity=False).write({
            'state': 'posted', 'payment_state': 'paid',
        })
        invoice._issue_consign_paid_invoice_grants()
        invoice._issue_consign_paid_invoice_grants()
        movements = order.consign_source_movement_ids
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements.movement_type, 'issue')
        self.assertEqual(movements.quantity, 4)
        self.assertTrue(movements.idempotency_key.startswith('consign:paid-invoice-grant:v1:'))
