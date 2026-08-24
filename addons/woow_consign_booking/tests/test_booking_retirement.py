# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestBookingConsignmentRetirement(TransactionCase):
    """Regression tests for retiring booking-side consignment redemption."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Consignment Retirement Customer',
            'email': 'retirement@example.com',
            'phone': '0912345678',
        })
        cls.payment_product = cls.env['product.product'].create({
            'name': 'Retirement Test Service',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.appointment_type = cls.env['appointment.type'].create({
            'name': 'Legacy Consignment Appointment',
            'slot_duration': 1.0,
            'max_booking_days': 30,
            'min_booking_hours': 0,
            'auto_confirm': False,
            'require_payment': False,
            'consign_enabled': True,
        })

    def _create_booking(self):
        start = fields.Datetime.now() + timedelta(days=1)
        return self.env['appointment.booking'].create({
            'appointment_type_id': self.appointment_type.id,
            'partner_id': self.partner.id,
            'guest_name': self.partner.name,
            'guest_email': self.partner.email,
            'guest_phone': self.partner.phone,
            'guest_count': 1,
            'start_datetime': start,
            'end_datetime': start + timedelta(hours=1),
        })

    def test_legacy_consign_configuration_does_not_reserve_or_change_state(self):
        booking = self._create_booking()

        self.assertEqual(booking.state, 'draft')
        self.assertFalse(booking.consign_line_id)
        self.assertEqual(booking.consign_reserved_qty, 0.0)

    def test_legacy_consign_configuration_does_not_create_redemption(self):
        booking = self._create_booking()

        booking.action_confirm()
        self.assertEqual(booking.state, 'confirmed')
        self.assertFalse(booking.consign_redemption_id)

        booking.action_cancel()
        self.assertEqual(booking.state, 'cancelled')
        self.assertFalse(booking.consign_redemption_id)

    def test_legacy_consign_flag_no_longer_blocks_payment_configuration(self):
        self.appointment_type.write({
            'require_payment': True,
            'payment_product_ids': [(6, 0, [self.payment_product.id])],
            'consign_enabled': True,
        })

        self.assertTrue(self.appointment_type.require_payment)
        self.assertTrue(self.appointment_type.consign_enabled)
