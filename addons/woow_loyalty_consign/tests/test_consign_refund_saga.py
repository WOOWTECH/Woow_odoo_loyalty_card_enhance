from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestConsignRefundSaga(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Refund Saga Customer',
            'company_id': cls.company.id,
        })
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'company_id': cls.company.id,
        })
        cls.saga_model = cls.env['loyalty.consign.refund.saga']

    def test_pending_replay_requires_exact_payload(self):
        saga = self.saga_model._open_refund(
            self.order, self.partner, self.company.currency_id,
            'test:refund-saga:replay', 0.0, [],
        )
        self.assertEqual(saga.state, 'pending')
        self.assertEqual(
            self.saga_model._open_refund(
                self.order, self.partner, self.company.currency_id,
                'test:refund-saga:replay', 0.0, [],
            ), saga,
        )
        with self.assertRaises(ValidationError):
            self.saga_model._open_refund(
                self.order, self.partner, self.company.currency_id,
                'test:refund-saga:replay', 1.0, [],
            )

    def test_only_done_terminally_completes_empty_reversal_saga(self):
        saga = self.saga_model._open_refund(
            self.order, self.partner, self.company.currency_id,
            'test:refund-saga:callback', 0.0, [],
        )
        saga._payment_callback('error')
        self.assertEqual(saga.state, 'error')
        saga._payment_callback('done')
        self.assertEqual(saga.state, 'done')
        self.assertEqual(saga._payment_callback('done'), saga)
