import inspect
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from odoo.addons.woow_loyalty_consign.models.loyalty_consign_engine import (
    LoyaltyConsignEngine,
)


class TestConsignAuthorize(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Authorize Owner', 'company_id': cls.env.company.id,
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Authorize Other', 'company_id': cls.env.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Authorize Variant A', 'type': 'service', 'list_price': 100,
        })
        attribute = cls.env['product.attribute'].create({
            'name': 'Authorize Kind',
        })
        attribute_values = cls.env['product.attribute.value'].create([
            {'name': 'A', 'attribute_id': attribute.id},
            {'name': 'B', 'attribute_id': attribute.id},
        ])
        template = cls.env['product.template'].create({
            'name': 'Authorize Variant Template', 'type': 'service',
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, attribute_values.ids)],
            })],
        })
        cls.variant_a, cls.variant_b = template.product_variant_ids
        cls.second_product = cls.env['product.product'].create({
            'name': 'Authorize Product B', 'type': 'service', 'list_price': 30,
        })
        cls.program = cls._program('Authorize Program')
        cls.second_program = cls._program('Authorize Program Two')

    @classmethod
    def _program(cls, name):
        return cls.env['loyalty.program'].create({
            'name': name, 'program_type': 'consign', 'active': True,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })

    def _issue(self, key, product=None, quantity=10, program=None, partner=None):
        product = product or self.product
        partner = partner or self.partner
        return self.env['loyalty.consign.engine']._issue(
            source=partner, partner=partner, program=program or self.program,
            grants=[{'product': product, 'quantity': quantity}],
            idempotency_key=key,
        )

    def _request(self, card, product=None, quantity=1, uom=None):
        product = product or self.product
        return {
            'card': card, 'product': product,
            'uom': uom or product.uom_id, 'quantity': quantity,
        }

    def _authorize(self, key, requests, source=None, partner=None):
        return self.env['loyalty.consign.engine']._authorize(
            source=source or self.partner, partner=partner or self.partner,
            requests=requests, idempotency_key=key,
        )

    def test_duplicate_requests_aggregate_and_fifo_multiple_issue_facts(self):
        first = self._issue('test:authorize:fifo:first', quantity=3)
        second = self._issue('test:authorize:fifo:second', quantity=7)
        card = first.movement_ids.card_id
        operation = self._authorize('test:authorize:fifo', [
            self._request(card, quantity=2),
            {'card_id': card.id, 'product_id': self.product.id,
             'product_uom_id': self.product.uom_id.id, 'qty': 4},
        ])
        hold = operation.hold_ids
        self.assertEqual(len(hold), 1)
        self.assertEqual(sum(hold.allocation_line_ids.mapped('quantity')), 6)
        self.assertEqual(hold.allocation_line_ids.mapped('issue_movement_id').ids, [
            first.movement_ids.id, second.movement_ids.id,
        ])
        self.assertEqual(hold.allocation_line_ids.mapped('quantity'), [3, 3])
        self.assertEqual(first.movement_ids.aggregate_line_id.qty_available, 4)
        self.assertEqual(operation.result_json['hold_id'], hold.id)
        self.assertEqual(operation.result_json['card_ids'], card.ids)

    def test_one_hold_supports_multiple_cards_and_products(self):
        first = self._issue('test:authorize:multi:first', quantity=4)
        second = self._issue(
            'test:authorize:multi:second', product=self.second_product, quantity=5,
        )
        other_card_issue = self._issue(
            'test:authorize:multi:card', quantity=6, program=self.second_program,
        )
        operation = self._authorize('test:authorize:multi', [
            self._request(first.movement_ids.card_id, quantity=2),
            self._request(second.movement_ids.card_id, self.second_product, 3),
            self._request(other_card_issue.movement_ids.card_id, quantity=1),
        ])
        self.assertEqual(len(operation.hold_ids), 1)
        self.assertEqual(set(operation.result_json['card_ids']), {
            first.movement_ids.card_id.id, other_card_issue.movement_ids.card_id.id,
        })
        self.assertEqual(set(operation.result_json['projection_ids']), {
            first.movement_ids.aggregate_line_id.id,
            second.movement_ids.aggregate_line_id.id,
            other_card_issue.movement_ids.aggregate_line_id.id,
        })

    def test_uom_normalization_rounding_and_exact_variant(self):
        issue = self._issue(
            'test:authorize:uom:issue', product=self.variant_a, quantity=12,
        )
        dozen = self.env['uom.uom'].create({
            'name': 'Authorize Dozen',
            'category_id': self.variant_a.uom_id.category_id.id,
            'uom_type': 'bigger', 'factor_inv': 12, 'rounding': 0.01,
        })
        operation = self._authorize('test:authorize:uom', [
            self._request(issue.movement_ids.card_id, self.variant_a, 0.5, dozen),
        ])
        self.assertEqual(operation.hold_ids.allocation_line_ids.quantity, 6)
        before = self.env['loyalty.consign.operation'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'projection is missing'):
            self._authorize('test:authorize:wrong-variant', [
                self._request(issue.movement_ids.card_id, self.variant_b, 1),
            ])
        self.assertEqual(self.env['loyalty.consign.operation'].search_count([]), before)

    def test_duplicate_fractional_requests_round_once_after_aggregation(self):
        issue = self._issue('test:authorize:fractional:issue', quantity=1)
        card = issue.movement_ids.card_id
        operation = self._authorize('test:authorize:fractional', [
            self._request(card, quantity=0.006),
            self._request(card, quantity=0.006),
        ])
        allocation = operation.hold_ids.allocation_line_ids
        self.assertEqual(len(allocation), 1)
        self.assertEqual(allocation.quantity, 0.01)

    def test_pure_validation_rejects_owner_company_state_uom_and_forged_authority(self):
        issue = self._issue('test:authorize:validation:issue')
        card = issue.movement_ids.card_id
        before = self.env['loyalty.consign.operation'].search_count([])
        cases = []
        cases.append(({'card': card, 'product': self.product, 'quantity': 0}, 'positive'))
        wrong_category = self.env.ref('uom.product_uom_hour')
        cases.append((self._request(card, uom=wrong_category), 'same category'))
        cases.append(({**self._request(card), 'price_unit': 0}, 'price or issue'))
        cases.append(({**self._request(card), 'issue_movement_id': issue.movement_ids.id},
                      'price or issue'))
        for index, (request, message) in enumerate(cases):
            with self.subTest(index=index), self.assertRaisesRegex(ValidationError, message):
                self._authorize(f'test:authorize:validation:{index}', [request])
        with self.assertRaisesRegex(ValidationError, 'owner'):
            self._authorize(
                'test:authorize:wrong-owner', [self._request(card)],
                source=self.other_partner, partner=self.other_partner,
            )
        other_company = self.env['res.company'].create({
            'name': 'Authorize Foreign Company',
        })
        foreign_source = self.env['res.partner'].sudo().create({
            'name': 'Authorize Foreign Source', 'company_id': other_company.id,
        })
        with self.assertRaisesRegex(ValidationError, 'another company'):
            self._authorize(
                'test:authorize:wrong-company', [self._request(card)],
                source=foreign_source,
            )
        ordinary_program = self.env['loyalty.program'].create({
            'name': 'Authorize Ordinary Loyalty', 'program_type': 'loyalty',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        ordinary_card = self.env['loyalty.card'].create({
            'program_id': ordinary_program.id, 'partner_id': self.partner.id,
            'points': 0,
        })
        with self.assertRaisesRegex(ValidationError, 'active consignment card'):
            self._authorize(
                'test:authorize:non-consign', [self._request(ordinary_card)],
            )
        card.active = False
        with self.assertRaisesRegex(ValidationError, 'active consignment card'):
            self._authorize('test:authorize:inactive', [self._request(card)])
        card.active = True
        card.program_id.active = False
        with self.assertRaisesRegex(ValidationError, 'active consignment program'):
            self._authorize('test:authorize:inactive-program', [self._request(card)])
        self.assertEqual(self.env['loyalty.consign.operation'].search_count([]), before)

    def test_all_or_nothing_insufficiency_creates_no_hold_or_allocation(self):
        first = self._issue('test:authorize:atomic:first', quantity=5)
        second = self._issue(
            'test:authorize:atomic:second', product=self.second_product, quantity=1,
        )
        hold_count = self.env['loyalty.consign.hold'].search_count([])
        allocation_count = self.env['loyalty.consign.hold.allocation'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'exceeds'):
            with self.env.cr.savepoint():
                self._authorize('test:authorize:atomic', [
                    self._request(first.movement_ids.card_id, quantity=2),
                    self._request(second.movement_ids.card_id, self.second_product, 2),
                ])
        self.assertEqual(self.env['loyalty.consign.hold'].search_count([]), hold_count)
        self.assertEqual(
            self.env['loyalty.consign.hold.allocation'].search_count([]), allocation_count,
        )
        self.assertEqual(first.movement_ids.aggregate_line_id.qty_available, 5)

    def test_expiry_is_exactly_thirty_minutes_and_replay_is_stable(self):
        issue = self._issue('test:authorize:replay:issue')
        before = fields.Datetime.now()
        operation = self._authorize(
            'test:authorize:replay', [self._request(issue.movement_ids.card_id, quantity=2)],
        )
        after = fields.Datetime.now()
        hold = operation.hold_ids
        self.assertGreaterEqual(hold.expires_at, before + timedelta(minutes=30))
        self.assertLessEqual(hold.expires_at, after + timedelta(minutes=30))
        replay = self._authorize(
            'test:authorize:replay', [self._request(issue.movement_ids.card_id, quantity=2)],
        )
        self.assertEqual(replay, operation)
        self.assertEqual(len(operation.hold_ids), 1)
        self.assertEqual(len(hold.allocation_line_ids), 1)
        with self.assertRaisesRegex(ValidationError, 'different payload'):
            self._authorize(
                'test:authorize:replay',
                [self._request(issue.movement_ids.card_id, quantity=3)],
            )

    def test_completed_replay_survives_later_card_or_program_deactivation(self):
        card_issue = self._issue('test:authorize:replay:card:issue')
        card = card_issue.movement_ids.card_id
        card_operation = self._authorize(
            'test:authorize:replay:card', [self._request(card, quantity=2)],
        )
        card.active = False
        self.assertEqual(
            self._authorize(
                'test:authorize:replay:card', [self._request(card, quantity=2)],
            ),
            card_operation,
        )
        with self.assertRaisesRegex(ValidationError, 'different payload'):
            self._authorize(
                'test:authorize:replay:card', [self._request(card, quantity=3)],
            )
        card.active = True

        program_issue = self._issue('test:authorize:replay:program:issue')
        program_card = program_issue.movement_ids.card_id
        program_operation = self._authorize(
            'test:authorize:replay:program', [self._request(program_card, quantity=2)],
        )
        program_card.program_id.active = False
        self.assertEqual(
            self._authorize(
                'test:authorize:replay:program',
                [self._request(program_card, quantity=2)],
            ),
            program_operation,
        )

    def test_expiration_cron_processes_multiple_expired_holds_in_one_bounded_batch(self):
        first = self._issue('test:authorize:cron:batch:first', quantity=2)
        second = self._issue(
            'test:authorize:cron:batch:second', quantity=2,
            program=self.second_program,
        )
        first_hold = self._authorize(
            'test:authorize:cron:batch:first:hold',
            [self._request(first.movement_ids.card_id, quantity=1)],
        ).hold_ids
        second_hold = self._authorize(
            'test:authorize:cron:batch:second:hold',
            [self._request(second.movement_ids.card_id, quantity=1)],
        ).hold_ids
        now = fields.Datetime.now()
        (first_hold | second_hold)._write_from_engine({
            'expires_at': now - timedelta(seconds=1),
        })
        self.assertEqual(
            self.env['loyalty.consign.hold']._cron_expire_holds(batch_size=2, now=now), 2,
        )
        self.assertEqual((first_hold | second_hold).mapped('state'), ['expired', 'expired'])

    def test_expiration_cron_is_bounded_idempotent_and_leaves_future_hold(self):
        cron = self.env.ref('woow_loyalty_consign.ir_cron_expire_consign_holds')
        self.assertTrue(cron.active)
        self.assertEqual(cron.code, 'model._cron_expire_holds()')
        issue = self._issue('test:authorize:cron:issue', quantity=10)
        card = issue.movement_ids.card_id
        expired = self._authorize(
            'test:authorize:cron:expired', [self._request(card, quantity=2)],
        ).hold_ids
        future = self._authorize(
            'test:authorize:cron:future', [self._request(card, quantity=3)],
        ).hold_ids
        expired._write_from_engine({
            'expires_at': fields.Datetime.now() - timedelta(seconds=1),
        })
        future._write_from_engine({
            'expires_at': fields.Datetime.now() + timedelta(minutes=10),
        })
        now = fields.Datetime.now()
        self.assertEqual(
            self.env['loyalty.consign.hold']._cron_expire_holds(batch_size=1, now=now), 1,
        )
        self.assertEqual(expired.state, 'expired')
        self.assertEqual(expired.expired_at, now)
        self.assertEqual(expired.transition_user_id, self.env.user)
        self.assertEqual(future.state, 'active')
        self.assertEqual(issue.movement_ids.aggregate_line_id.qty_available, 7)
        self.assertEqual(
            self.env['loyalty.consign.hold']._cron_expire_holds(batch_size=1, now=now), 0,
        )

    def test_engine_api_is_private(self):
        own_methods = {
            name for name, value in LoyaltyConsignEngine.__dict__.items()
            if callable(value) and not name.startswith('_')
        }
        self.assertFalse(own_methods)
        self.assertIn('_authorize', inspect.getsource(LoyaltyConsignEngine))
