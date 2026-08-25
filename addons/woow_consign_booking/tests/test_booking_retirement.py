# -*- coding: utf-8 -*-

import inspect
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase

from odoo.addons.woow_consign_booking.models.appointment_booking import AppointmentBooking


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

    def test_historical_reserved_quantity_never_reduces_core_availability(self):
        program = self.env['loyalty.program'].create({
            'name': 'Booking Retirement Program',
            'program_type': 'consign',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        card = self.env['loyalty.card'].create({
            'program_id': program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })
        line = self.env['loyalty.consign.line'].create({
            'card_id': card.id,
            'product_id': self.payment_product.id,
            'qty_deposited': 5,
            'unit_price': self.payment_product.list_price,
        })

        line.write({'reserved_qty': 4})

        self.assertEqual(line.reserved_qty, 4)
        self.assertEqual(line.qty_available, 5)

    def test_booking_install_keeps_task6_lifecycle_private_and_unwired(self):
        engine = self.env['loyalty.consign.engine']
        for method in ('_capture', '_release', '_reverse_redeem', '_clawback_issue'):
            with self.subTest(method=method):
                self.assertTrue(callable(getattr(engine, method, None)))
        source = inspect.getsource(AppointmentBooking)
        for private_lifecycle_call in (
            '_authorize(', '_capture(', '_release(', '_reverse_redeem(',
            '_clawback_issue(', '_append_movement(',
        ):
            with self.subTest(call=private_lifecycle_call):
                self.assertNotIn(private_lifecycle_call, source)

    def test_all_legacy_booking_consign_views_are_inactive(self):
        view_xmlids = (
            'woow_consign_booking.appointment_book_consign_info',
            'woow_consign_booking.appointment_booking_consign_form',
            'woow_consign_booking.appointment_confirm_consign_info',
            'woow_consign_booking.appointment_type_consign_form',
            'woow_consign_booking.loyalty_card_consign_add_reserved',
            'woow_consign_booking.loyalty_consign_redemption_add_booking',
            'woow_consign_booking.portal_booking_consign_sidebar',
        )

        for xmlid in view_xmlids:
            with self.subTest(xmlid=xmlid):
                self.assertFalse(self.env.ref(xmlid).active)
