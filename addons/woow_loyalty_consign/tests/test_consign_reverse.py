import inspect
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from odoo.addons.woow_loyalty_consign.models.loyalty_consign_engine import (
    LoyaltyConsignEngine,
)


class TestConsignCaptureRelease(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Lifecycle Owner', 'company_id': cls.env.company.id,
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Lifecycle Other Owner', 'company_id': cls.env.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Lifecycle Product', 'type': 'service', 'list_price': 123,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Lifecycle Program', 'program_type': 'consign', 'active': True,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })

    def _issue(self, key, quantity):
        return self.env['loyalty.consign.engine']._issue(
            source=self.partner,
            partner=self.partner,
            program=self.program,
            grants=[{'product': self.product, 'quantity': quantity}],
            idempotency_key=key,
        )

    def _hold(self, key, quantity=2, issue_quantity=10):
        issue = self._issue(f'{key}:issue', issue_quantity)
        card = issue.movement_ids.card_id
        authorization = self.env['loyalty.consign.engine']._authorize(
            source=self.partner,
            partner=self.partner,
            requests=[{
                'card_id': card.id,
                'product_id': self.product.id,
                'uom_id': self.product.uom_id.id,
                'quantity': quantity,
            }],
            idempotency_key=f'{key}:authorize',
        )
        return authorization.hold_ids, issue.movement_ids

    def _capture(self, key, hold):
        return self.env['loyalty.consign.engine']._capture(
            source=self.partner, partner=self.partner, hold=hold,
            idempotency_key=key,
        )

    def _release(self, key, hold):
        return self.env['loyalty.consign.engine']._release(
            source=self.partner, partner=self.partner, hold=hold,
            idempotency_key=key,
        )

    def _reverse_redeem(self, key, redeem, quantity):
        return self.env['loyalty.consign.engine']._reverse_redeem(
            source=self.partner, partner=self.partner, redeem_movement=redeem,
            quantity=quantity, idempotency_key=key,
        )

    def _clawback_issue(self, key, issue, quantity):
        return self.env['loyalty.consign.engine']._clawback_issue(
            source=self.partner, partner=self.partner, issue_movement=issue,
            quantity=quantity, idempotency_key=key,
        )

    def test_capture_appends_exact_redeems_and_preserves_allocation_value(self):
        first = self._issue('test:lifecycle:capture:first', 3)
        second = self._issue('test:lifecycle:capture:second', 7)
        card = first.movement_ids.card_id
        authorization = self.env['loyalty.consign.engine']._authorize(
            source=self.partner,
            partner=self.partner,
            requests=[{
                'card_id': card.id,
                'product_id': self.product.id,
                'uom_id': self.product.uom_id.id,
                'quantity': 6,
            }],
            idempotency_key='test:lifecycle:capture:authorize',
        )
        hold = authorization.hold_ids
        line = first.movement_ids.aggregate_line_id
        self.assertEqual(line.qty_available, 4)

        operation = self._capture('test:lifecycle:capture', hold)
        redeems = operation.movement_ids.sorted('original_movement_id')
        self.assertEqual(hold.state, 'captured')
        self.assertEqual(len(redeems), 2)
        self.assertEqual(
            set(redeems.mapped('original_movement_id').ids),
            {first.movement_ids.id, second.movement_ids.id},
        )
        self.assertEqual(sorted(redeems.mapped('quantity')), [3, 3])
        self.assertTrue(all(
            redeem.unit_value == redeem.original_movement_id.unit_value
            for redeem in redeems
        ))
        self.assertEqual(operation.result_json['hold_id'], hold.id)
        self.assertEqual(line.qty_redeemed, 6)
        self.assertEqual(line.qty_on_hold, 0)
        self.assertEqual(line.qty_available, 4)

    def test_release_appends_no_movement_and_restores_availability(self):
        hold, issue = self._hold('test:lifecycle:release', quantity=4)
        line = issue.aggregate_line_id
        movement_count = self.env['loyalty.consign.movement'].search_count([])
        self.assertEqual(line.qty_available, 6)

        operation = self._release('test:lifecycle:release', hold)

        self.assertEqual(hold.state, 'released')
        self.assertEqual(operation.movement_ids, self.env['loyalty.consign.movement'])
        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([]), movement_count,
        )
        self.assertEqual(line.qty_on_hold, 0)
        self.assertEqual(line.qty_available, 10)

    def test_completed_capture_and_release_replay_after_card_program_deactivation(self):
        capture_hold, _issue = self._hold('test:lifecycle:replay:capture')
        capture = self._capture('test:lifecycle:replay:capture', capture_hold)
        capture_hold.allocation_line_ids.card_id.active = False
        capture_hold.allocation_line_ids.card_id.program_id.active = False
        replay = self._capture('test:lifecycle:replay:capture', capture_hold)
        self.assertEqual(replay, capture)

        capture_hold.allocation_line_ids.card_id.active = True
        self.program.active = True
        release_hold, _issue = self._hold('test:lifecycle:replay:release')
        release = self._release('test:lifecycle:replay:release', release_hold)
        release_hold.allocation_line_ids.card_id.active = False
        release_hold.allocation_line_ids.card_id.program_id.active = False
        replay = self._release('test:lifecycle:replay:release', release_hold)
        self.assertEqual(replay, release)

    def test_same_key_payload_mismatch_and_invalid_lifecycle_paths_add_no_movements(self):
        first, _issue = self._hold('test:lifecycle:invalid:first')
        second, _issue = self._hold('test:lifecycle:invalid:second')
        self._capture('test:lifecycle:invalid:key', first)
        with self.assertRaisesRegex(ValidationError, 'different payload'):
            self._capture('test:lifecycle:invalid:key', second)

        active, _issue = self._hold('test:lifecycle:invalid:active')
        movement_count = self.env['loyalty.consign.movement'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'exact source, company, and customer'):
            self.env['loyalty.consign.engine']._capture(
                source=self.other_partner,
                partner=self.other_partner,
                hold=active,
                idempotency_key='test:lifecycle:invalid:partner',
            )
        foreign_company = self.env['res.company'].create({'name': 'Lifecycle Foreign'})
        foreign_source = self.env['res.partner'].sudo().create({
            'name': 'Lifecycle Foreign Source', 'company_id': foreign_company.id,
        })
        with self.assertRaisesRegex(ValidationError, 'another company'):
            self.env['loyalty.consign.engine']._release(
                source=foreign_source,
                partner=self.partner,
                hold=active,
                idempotency_key='test:lifecycle:invalid:company',
            )
        active._write_from_engine({
            'expires_at': fields.Datetime.now() - timedelta(seconds=1),
        })
        with self.assertRaisesRegex(ValidationError, 'unexpired Hold'):
            self._capture('test:lifecycle:invalid:expired', active)
        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([]), movement_count,
        )
        active._write_from_engine({'state': 'released'})
        with self.assertRaisesRegex(ValidationError, 'active Hold'):
            self._release('test:lifecycle:invalid:released', active)

    def test_redeem_reversal_is_append_only_exact_value_capped_and_replayed(self):
        hold, _issue = self._hold('test:lifecycle:reverse:redeem', quantity=3)
        redeem = self._capture('test:lifecycle:reverse:redeem:capture', hold).movement_ids
        reversal = self._reverse_redeem(
            'test:lifecycle:reverse:redeem', redeem, 2,
        )
        movement = reversal.movement_ids
        line = redeem.aggregate_line_id
        self.assertEqual(movement.movement_type, 'redeem_reversal')
        self.assertEqual(movement.original_movement_id, redeem)
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(movement.unit_value, redeem.unit_value)
        self.assertEqual(movement.value_delta, redeem.value_delta * 2 / redeem.quantity)
        line.invalidate_recordset()
        self.assertEqual(line.qty_redeemed, 3)
        self.assertEqual(line.qty_reversed, 2)
        self.assertEqual(line.qty_available, 9)
        self.assertEqual(
            self._reverse_redeem('test:lifecycle:reverse:redeem', redeem, 2), reversal,
        )
        movement_count = self.env['loyalty.consign.movement'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'cannot exceed its original'):
            self._reverse_redeem('test:lifecycle:reverse:redeem:over', redeem, 2)
        with self.assertRaisesRegex(ValidationError, 'different payload'):
            self._reverse_redeem('test:lifecycle:reverse:redeem', redeem, 1)
        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([]), movement_count,
        )
        self.assertFalse(reversal.result_json.get('payment_id'))
        self.assertFalse(reversal.result_json.get('cash_amount'))

    def test_issue_clawback_is_unused_unheld_and_source_safe(self):
        issue = self._issue('test:lifecycle:clawback:issue', 10).movement_ids
        line = issue.aggregate_line_id
        reversal = self._clawback_issue('test:lifecycle:clawback', issue, 3)
        self.assertEqual(reversal.movement_ids.movement_type, 'issue_reversal')
        self.assertEqual(reversal.movement_ids.original_movement_id, issue)
        self.assertEqual(reversal.movement_ids.unit_value, issue.unit_value)
        self.assertEqual(line.qty_available, 7)
        self.assertEqual(self._clawback_issue('test:lifecycle:clawback', issue, 3), reversal)

        movement_count = self.env['loyalty.consign.movement'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'exceed'):
            self._clawback_issue('test:lifecycle:clawback:over', issue, 8)
        with self.assertRaisesRegex(ValidationError, 'exact source, company, and customer'):
            self.env['loyalty.consign.engine']._clawback_issue(
                source=self.other_partner, partner=self.other_partner,
                issue_movement=issue, quantity=1,
                idempotency_key='test:lifecycle:clawback:wrong-partner',
            )
        foreign_company = self.env['res.company'].create({
            'name': 'Lifecycle Clawback Foreign Company',
        })
        foreign_source = self.env['res.partner'].sudo().create({
            'name': 'Lifecycle Clawback Foreign Source',
            'company_id': foreign_company.id,
        })
        with self.assertRaisesRegex(ValidationError, 'another company'):
            self.env['loyalty.consign.engine']._clawback_issue(
                source=foreign_source, partner=self.partner,
                issue_movement=issue, quantity=1,
                idempotency_key='test:lifecycle:clawback:wrong-company',
            )
        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([]), movement_count,
        )

    def test_issue_clawback_rejects_consumed_or_active_held_capacity(self):
        consumed_hold, consumed_issue = self._hold(
            'test:lifecycle:clawback:consumed', quantity=2,
        )
        self._capture('test:lifecycle:clawback:consumed:capture', consumed_hold)
        movement_count = self.env['loyalty.consign.movement'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'outgoing movement cannot exceed'):
            self._clawback_issue(
                'test:lifecycle:clawback:consumed', consumed_issue, 9,
            )
        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([]), movement_count,
        )

        held_hold, _held_issue = self._hold(
            'test:lifecycle:clawback:held', quantity=2,
        )
        held_issue = held_hold.allocation_line_ids.issue_movement_id
        with self.assertRaisesRegex(ValidationError, 'active Consignment Hold'):
            self._clawback_issue('test:lifecycle:clawback:held', held_issue, 1)
        self.assertEqual(held_hold.state, 'active')

    def test_active_hold_blocks_direct_consign_card_deactivation_then_release_allows_it(self):
        hold, issue = self._hold('test:lifecycle:deactivation', quantity=2)
        card = issue.card_id
        with self.assertRaisesRegex(ValidationError, 'active Hold cannot be deactivated'):
            card.active = False
        self.assertTrue(card.active)
        self._release('test:lifecycle:deactivation:release', hold)
        card.active = False
        self.assertFalse(card.active)

        ordinary_program = self.env['loyalty.program'].create({
            'name': 'Lifecycle Ordinary Program', 'program_type': 'loyalty',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        ordinary_card = self.env['loyalty.card'].create({
            'program_id': ordinary_program.id, 'partner_id': self.partner.id,
            'points': 0,
        })
        ordinary_card.active = False
        self.assertFalse(ordinary_card.active)

    def test_engine_lifecycle_methods_are_private(self):
        own_methods = {
            name for name, value in LoyaltyConsignEngine.__dict__.items()
            if callable(value) and not name.startswith('_')
        }
        self.assertFalse(own_methods)
        source = inspect.getsource(LoyaltyConsignEngine)
        self.assertIn('def _capture(', source)
        self.assertIn('def _release(', source)
        self.assertIn('def _reverse_redeem(', source)
        self.assertIn('def _clawback_issue(', source)
