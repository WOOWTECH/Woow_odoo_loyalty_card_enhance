from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user


class TestConsignGrants(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Consign Grant Customer',
        })
        cls.trigger = cls._product('Treatment Package', 1000.0)
        cls.treatment = cls._product('Treatment Entitlement', 100.0)
        cls.aftercare = cls._product('Aftercare Entitlement', 25.0)
        cls.unrelated = cls._product('Unrelated Sale Product', 50.0)

    @classmethod
    def _product(cls, name, list_price=0.0):
        return cls.env['product.product'].create({
            'name': name,
            'type': 'service',
            'list_price': list_price,
        })

    def _program(
        self,
        name,
        trigger,
        grants,
        company='current',
        program_type='consign',
        active=True,
    ):
        company_id = self.env.company.id if company == 'current' else company
        currency_company = (
            self.env['res.company'].browse(company_id)
            if company_id else self.env.company
        )
        return self.env['loyalty.program'].create({
            'name': name,
            'program_type': program_type,
            'active': active,
            'company_id': company_id,
            'currency_id': currency_company.currency_id.id,
            'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': product.id,
                    'product_uom_id': uom.id,
                    'quantity': quantity,
                }) for product, uom, quantity in grants],
            })],
        })

    def _order(self, lines):
        order_lines = []
        for line in lines:
            product, quantity = line[:2]
            uom = line[2] if len(line) > 2 else product.uom_id
            order_lines.append((0, 0, {
                'product_id': product.id,
                'name': product.name,
                'product_uom_qty': quantity,
                'product_uom': uom.id,
                'price_unit': product.list_price,
            }))
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': order_lines,
        })

    def _non_base_uom(self, name, product, uom_type, ratio):
        vals = {
            'name': name,
            'category_id': product.uom_id.category_id.id,
            'uom_type': uom_type,
            'rounding': 0.01,
        }
        if uom_type == 'bigger':
            vals['factor_inv'] = ratio
        else:
            vals['factor'] = ratio
        return self.env['uom.uom'].create(vals)

    def _capture_creation_notifications(self):
        contexts = []

        def capture(cards, force_send=False):
            if not (
                cards.env.context.get('loyalty_no_mail')
                or cards.env.context.get('action_no_send_mail')
            ):
                contexts.append(dict(cards.env.context))

        return contexts, patch.object(
            type(self.env['loyalty.card']),
            '_send_creation_communication',
            new=capture,
        )

    def _issued_lines(self, program, order):
        card = self.env['loyalty.card'].search([
            ('program_id', '=', program.id),
            ('partner_id', '=', self.partner.id),
        ])
        self.assertEqual(len(card), 1)
        return card.consign_line_ids.filtered(
            lambda line: line.sale_order_id == order
        )

    def test_one_trigger_grants_multiple_entitlement_products(self):
        program = self._program('Multi-grant Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 10.0),
            (self.aftercare, self.aftercare.uom_id, 2.0),
        ])
        order = self._order([(self.trigger, 1.0)])

        order.action_confirm()

        issued = self._issued_lines(program, order)
        self.assertEqual(set(issued.product_id.ids), {
            self.treatment.id,
            self.aftercare.id,
        })
        self.assertEqual(
            issued.filtered(lambda line: line.product_id == self.treatment).qty_deposited,
            10.0,
        )
        self.assertEqual(
            issued.filtered(lambda line: line.product_id == self.aftercare).qty_deposited,
            2.0,
        )
        self.assertEqual(issued.sale_line_id.product_id, self.trigger)
        self.assertEqual(order.consign_line_count, 2)
        self.assertEqual(
            set(order.action_view_consign_lines()['domain'][0][2]),
            set(issued.ids),
        )
        movements = issued.mapped('movement_ids')
        self.assertEqual(len(movements), 2)
        self.assertEqual(set(movements.mapped('movement_type')), {'issue'})
        self.assertEqual(
            set(movements.mapped('product_uom_id').ids),
            {self.treatment.uom_id.id, self.aftercare.uom_id.id},
        )
        source_action = movements[:1].action_open_source()
        self.assertEqual(source_action['res_model'], 'sale.order')
        self.assertEqual(source_action['res_id'], order.id)

    def test_trigger_quantity_multiplies_configured_grants(self):
        program = self._program('Quantity Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 3.0),
        ])
        order = self._order([(self.trigger, 2.0)])

        order.action_confirm()

        issued = self._issued_lines(program, order)
        self.assertEqual(issued.qty_deposited, 6.0)

    def test_new_card_notification_uses_non_suppressed_context(self):
        self._program('Notification Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ])
        order = self._order([(self.trigger, 1.0)])
        notification_contexts, notification_patch = (
            self._capture_creation_notifications()
        )

        with notification_patch:
            order.action_confirm()

        self.assertEqual(len(notification_contexts), 1)
        self.assertFalse(notification_contexts[0].get('loyalty_no_mail'))
        self.assertFalse(notification_contexts[0].get('action_no_send_mail'))

    def test_larger_non_base_trigger_and_entitlement_uoms_are_converted(self):
        trigger_dozen = self._non_base_uom(
            'Grant Trigger Dozen', self.trigger, 'bigger', 12.0,
        )
        entitlement_dozen = self._non_base_uom(
            'Grant Entitlement Dozen', self.treatment, 'bigger', 12.0,
        )
        program = self._program('Larger UoM Program', self.trigger, [
            (self.treatment, entitlement_dozen, 0.5),
        ])
        order = self._order([(self.trigger, 2.0, trigger_dozen)])

        order.action_confirm()

        # 2 trigger dozens = 24 base triggers; each grants 0.5 entitlement
        # dozen = 6 base entitlements, for 144 base entitlements total.
        issued = self._issued_lines(program, order)
        self.assertEqual(issued.qty_deposited, 144.0)

    def test_smaller_non_base_trigger_and_entitlement_uoms_are_converted(self):
        trigger_tenth = self._non_base_uom(
            'Grant Trigger Tenth', self.trigger, 'smaller', 10.0,
        )
        entitlement_tenth = self._non_base_uom(
            'Grant Entitlement Tenth', self.treatment, 'smaller', 10.0,
        )
        program = self._program('Smaller UoM Program', self.trigger, [
            (self.treatment, entitlement_tenth, 30.0),
        ])
        order = self._order([(self.trigger, 20.0, trigger_tenth)])

        order.action_confirm()

        # 20 trigger tenths = 2 base triggers; each grants 30 entitlement
        # tenths = 3 base entitlements, for 6 base entitlements total.
        issued = self._issued_lines(program, order)
        self.assertEqual(issued.qty_deposited, 6.0)

    def test_unrelated_order_line_is_not_deposited(self):
        program = self._program('Explicit-only Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 4.0),
        ])
        order = self._order([
            (self.trigger, 1.0),
            (self.unrelated, 7.0),
        ])

        order.action_confirm()

        issued = self._issued_lines(program, order)
        self.assertEqual(issued.product_id, self.treatment)
        self.assertEqual(issued.qty_deposited, 4.0)
        self.assertFalse(order.order_line.filtered(
            lambda line: line.product_id == self.unrelated
        ).is_consigned)

    def test_program_without_explicit_grants_does_not_create_empty_card(self):
        program = self.env['loyalty.program'].create({
            'name': 'No Empty Card Program',
            'program_type': 'consign',
            'active': True,
            'company_id': self.env.company.id,
        })
        order = self._order([(self.trigger, 1.0)])

        order.action_confirm()

        self.assertFalse(self.env['loyalty.card'].search([
            ('program_id', '=', program.id),
            ('partner_id', '=', self.partner.id),
        ]))

    def test_same_sale_line_source_can_accumulate(self):
        program = self._program('Same Source Accumulation Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
            (self.treatment, self.treatment.uom_id, 2.0),
        ])
        order = self._order([(self.trigger, 1.0)])

        order.action_confirm()

        issued = self._issued_lines(program, order)
        self.assertEqual(len(issued), 1)
        self.assertEqual(issued.qty_deposited, 3.0)
        self.assertEqual(issued.sale_line_id, order.order_line)
        self.assertEqual(order.consign_line_count, 1)
        self.assertEqual(len(issued.movement_ids), 2)
        self.assertEqual(sum(issued.movement_ids.mapped('quantity')), 3.0)
        self.assertEqual(len(issued.movement_ids.mapped('operation_id')), 2)

    def test_second_order_reuses_card_accumulates_and_does_not_renotify(self):
        program = self._program('Card Reuse Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 2.0),
        ])
        first_order = self._order([(self.trigger, 1.0)])
        second_order = self._order([(self.trigger, 2.0)])
        notification_contexts, notification_patch = (
            self._capture_creation_notifications()
        )

        with notification_patch:
            first_order.action_confirm()
            second_order.action_confirm()

        cards = self.env['loyalty.card'].search([
            ('program_id', '=', program.id),
            ('partner_id', '=', self.partner.id),
        ])
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards.consign_line_ids), 2)
        self.assertEqual(sum(cards.consign_line_ids.mapped('qty_deposited')), 6.0)
        self.assertEqual(
            set(cards.consign_line_ids.mapped('sale_order_id').ids),
            {first_order.id, second_order.id},
        )
        self.assertEqual(first_order.consign_line_count, 1)
        self.assertEqual(second_order.consign_line_count, 1)
        self.assertEqual(
            first_order.action_view_consign_lines()['domain'],
            [('id', 'in', first_order.consign_line_ids.ids)],
        )
        self.assertEqual(
            second_order.action_view_consign_lines()['domain'],
            [('id', 'in', second_order.consign_line_ids.ids)],
        )
        self.assertEqual(len(notification_contexts), 1)
        self.assertFalse(notification_contexts[0].get('loyalty_no_mail'))
        movements = cards.consign_movement_ids
        self.assertEqual(len(movements), 2)
        self.assertEqual(
            set(movements.mapped('source_res_id')),
            {first_order.order_line.id, second_order.order_line.id},
        )
        self.assertEqual(first_order.consign_source_movement_count, 1)
        self.assertEqual(second_order.consign_source_movement_count, 1)

    def test_grant_adapter_reinvocation_does_not_duplicate_movement(self):
        program = self._program('Replay Grant Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 2.0),
        ])
        order = self._order([(self.trigger, 1.0)])
        order.action_confirm()
        movement = self._issued_lines(program, order).movement_ids

        order._action_create_consign_card()

        self.assertEqual(
            self.env['loyalty.consign.movement'].search_count([
                ('id', '=', movement.id),
            ]),
            1,
        )
        self.assertEqual(len(order.consign_source_movement_ids), 1)

    def test_two_programs_each_receive_only_their_own_grants(self):
        second_trigger = self._product('Aftercare Package', 500.0)
        first_program = self._program('Treatment Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 5.0),
        ])
        second_program = self._program('Aftercare Program', second_trigger, [
            (self.aftercare, self.aftercare.uom_id, 3.0),
        ])
        order = self._order([
            (self.trigger, 1.0),
            (second_trigger, 2.0),
            (self.unrelated, 9.0),
        ])

        order.action_confirm()

        first_lines = self._issued_lines(first_program, order)
        second_lines = self._issued_lines(second_program, order)
        self.assertEqual(first_lines.product_id, self.treatment)
        self.assertEqual(first_lines.qty_deposited, 5.0)
        self.assertEqual(second_lines.product_id, self.aftercare)
        self.assertEqual(second_lines.qty_deposited, 6.0)
        self.assertEqual(first_lines.movement_ids.card_id, first_lines.card_id)
        self.assertEqual(second_lines.movement_ids.card_id, second_lines.card_id)
        self.assertNotEqual(first_lines.movement_ids.card_id, second_lines.movement_ids.card_id)

    def test_direct_empty_grant_header_is_rejected(self):
        program = self.env['loyalty.program'].create({
            'name': 'Direct Empty Header Program',
            'program_type': 'consign',
            'active': False,
            'company_id': self.env.company.id,
        })

        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.grant.rule'].create({
                'program_id': program.id,
                'trigger_product_id': self.trigger.id,
            })

    def test_grant_line_with_omitted_quantity_is_rejected(self):
        program = self._program('Omitted Quantity Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ], active=False)

        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.grant.line'].create({
                'rule_id': program.consign_grant_rule_ids.id,
                'entitlement_product_id': self.aftercare.id,
                'product_uom_id': self.aftercare.uom_id.id,
            })

    def test_trigger_lock_helper_orders_exact_products_deterministically(self):
        second_trigger = self._product('Lock Order Trigger', 100.0)
        first_program = self._program('First Lock Program', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ], active=False)
        second_program = self._program('Second Lock Program', second_trigger, [
            (self.aftercare, self.aftercare.uom_id, 1.0),
        ], active=False)
        rules = (
            second_program.consign_grant_rule_ids
            | first_program.consign_grant_rule_ids
        )

        expected_ids = sorted([self.trigger.id, second_trigger.id])
        self.assertEqual(rules._trigger_product_lock_ids(), expected_ids)

        with patch.object(
            type(self.env.cr), 'execute', autospec=True,
        ) as execute:
            rules._lock_trigger_product_ids([
                second_trigger.id,
                self.trigger.id,
                second_trigger.id,
            ])

        calls = [
            (' '.join(call.args[1].split()), call.args[2])
            for call in execute.call_args_list
        ]
        self.assertEqual(calls, [
            (
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (1129270867, expected_ids[0]),
            ),
            (
                'UPDATE product_product SET write_date = write_date '
                'WHERE id = %s RETURNING id',
                (expected_ids[0],),
            ),
            (
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (1129270867, expected_ids[1]),
            ),
            (
                'UPDATE product_product SET write_date = write_date '
                'WHERE id = %s RETURNING id',
                (expected_ids[1],),
            ),
        ])

    def test_card_creation_partner_lock_uses_serialization_token_update(self):
        order = self._order([(self.trigger, 1.0)])

        with patch.object(
            type(self.env.cr), 'execute', autospec=True,
        ) as execute:
            order._lock_consign_card_partner()

        execute.assert_called_once()
        self.assertEqual(
            ' '.join(execute.call_args.args[1].split()),
            'UPDATE res_partner SET write_date = write_date '
            'WHERE id = %s RETURNING id',
        )
        self.assertEqual(
            execute.call_args.args[2],
            (self.partner.id,),
        )

    def test_duplicate_overlapping_active_trigger_headers_are_rejected(self):
        grant = [(self.treatment, self.treatment.uom_id, 1.0)]
        self._program('First Overlap', self.trigger, grant)

        with self.assertRaises(ValidationError):
            self._program('Second Overlap', self.trigger, grant)

    def test_program_cannot_repeat_a_trigger_header(self):
        program = self._program('No Duplicate Headers', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ], active=False)

        with self.assertRaises(ValidationError):
            program.write({'consign_grant_rule_ids': [(0, 0, {
                'trigger_product_id': self.trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': self.aftercare.id,
                    'product_uom_id': self.aftercare.uom_id.id,
                    'quantity': 1.0,
                })],
            })]})

    def test_batch_create_rejects_duplicate_program_trigger_pairs(self):
        program = self.env['loyalty.program'].create({
            'name': 'Batch Duplicate Headers Program',
            'program_type': 'consign',
            'active': False,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        grant_line_vals = {
            'entitlement_product_id': self.treatment.id,
            'product_uom_id': self.treatment.uom_id.id,
            'quantity': 1.0,
        }

        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.grant.rule'].create([
                {
                    'program_id': program.id,
                    'trigger_product_id': self.trigger.id,
                    'grant_line_ids': [(0, 0, grant_line_vals)],
                },
                {
                    'program_id': program.id,
                    'trigger_product_id': self.trigger.id,
                    'grant_line_ids': [(0, 0, grant_line_vals)],
                },
            ])

    def test_different_explicit_companies_may_reuse_trigger(self):
        other_company = self.env['res.company'].create({
            'name': 'Other Grant Company',
        })
        grant = [(self.treatment, self.treatment.uom_id, 1.0)]

        first = self._program(
            'Current Company Program', self.trigger, grant,
            company=self.env.company.id,
        )
        second = self._program(
            'Other Company Program', self.trigger, grant,
            company=other_company.id,
        )

        self.assertTrue(first.consign_grant_rule_ids)
        self.assertTrue(second.consign_grant_rule_ids)

    def test_grant_record_rules_isolate_disallowed_companies_and_allow_global(self):
        other_company = self.env['res.company'].create({
            'name': 'Grant Security Other Company',
        })
        other_trigger = self._product('Grant Security Other Trigger')
        global_trigger = self._product('Grant Security Global Trigger')
        current_program = self._program('Visible Company Grants', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ], company=self.env.company.id)
        other_program = self._program('Hidden Company Grants', other_trigger, [
            (self.aftercare, self.aftercare.uom_id, 1.0),
        ], company=other_company.id)
        global_program = self._program('Global Grants', global_trigger, [
            (self.unrelated, self.unrelated.uom_id, 1.0),
        ], company=False)
        grant_user = new_test_user(
            self.env,
            login='consign_grant_company_user',
            groups='sales_team.group_sale_manager',
            company_id=self.env.company.id,
            company_ids=[(6, 0, [self.env.company.id])],
        )
        allowed_context = {'allowed_company_ids': [self.env.company.id]}
        all_rules = (
            current_program.consign_grant_rule_ids
            | other_program.consign_grant_rule_ids
            | global_program.consign_grant_rule_ids
        )
        visible_rules = self.env['loyalty.consign.grant.rule'].with_user(
            grant_user
        ).with_context(**allowed_context).search([
            ('id', 'in', all_rules.ids),
        ])
        visible_lines = self.env['loyalty.consign.grant.line'].with_user(
            grant_user
        ).with_context(**allowed_context).search([
            ('rule_id', 'in', all_rules.ids),
        ])

        self.assertEqual(
            set(visible_rules.ids),
            set((
                current_program.consign_grant_rule_ids
                | global_program.consign_grant_rule_ids
            ).ids),
        )
        self.assertEqual(
            set(visible_lines.ids),
            set((
                current_program.consign_grant_rule_ids.grant_line_ids
                | global_program.consign_grant_rule_ids.grant_line_ids
            ).ids),
        )
        self.assertEqual(
            other_program.consign_grant_rule_ids.grant_line_ids.company_id,
            other_company,
        )
        with self.assertRaises(AccessError):
            other_program.consign_grant_rule_ids.with_user(
                grant_user
            ).with_context(**allowed_context).write({
                'trigger_product_id': other_trigger.id,
            })

    def test_overlap_search_sees_conflict_hidden_by_company_rules(self):
        other_company = self.env['res.company'].create({
            'name': 'Hidden Conflict Company',
        })
        hidden_program = self._program('Hidden Conflict Grants', self.trigger, [
            (self.treatment, self.treatment.uom_id, 1.0),
        ], company=other_company.id)
        global_program = self.env['loyalty.program'].create({
            'name': 'Global Conflict Candidate',
            'program_type': 'consign',
            'active': True,
            'company_id': False,
            'currency_id': self.env.company.currency_id.id,
        })
        grant_user = new_test_user(
            self.env,
            login='consign_grant_invariant_user',
            groups='sales_team.group_sale_manager',
            company_id=self.env.company.id,
            company_ids=[(6, 0, [self.env.company.id])],
        )
        user_rules = self.env['loyalty.consign.grant.rule'].with_user(
            grant_user
        ).with_context(allowed_company_ids=[self.env.company.id])
        self.assertFalse(user_rules.search([
            ('id', '=', hidden_program.consign_grant_rule_ids.id),
        ]))

        with self.assertRaises(ValidationError):
            user_rules.create({
                'program_id': global_program.id,
                'trigger_product_id': self.trigger.id,
                'grant_line_ids': [(0, 0, {
                    'entitlement_product_id': self.aftercare.id,
                    'product_uom_id': self.aftercare.uom_id.id,
                    'quantity': 1.0,
                })],
            })

    def test_global_company_rule_conflicts_with_explicit_company(self):
        grant = [(self.treatment, self.treatment.uom_id, 1.0)]
        self._program(
            'Explicit Company Program', self.trigger, grant,
            company=self.env.company.id,
        )

        with self.assertRaises(ValidationError):
            self._program(
                'Global Company Program', self.trigger, grant,
                company=False,
            )

    def test_non_consign_program_rejects_grant_rules(self):
        with self.assertRaises(ValidationError):
            self._program(
                'Not a Consign Program',
                self.trigger,
                [(self.treatment, self.treatment.uom_id, 1.0)],
                program_type='loyalty',
            )

    def test_invalid_and_uom_rounded_zero_quantities_are_rejected(self):
        for quantity in (0.0, -1.0, self.treatment.uom_id.rounding / 10.0):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                self._program(
                    'Invalid Quantity %s' % quantity,
                    self.trigger,
                    [(self.treatment, self.treatment.uom_id, quantity)],
                )

    def test_entitlement_uom_category_must_match_product(self):
        mismatched_uom = self.env['uom.uom'].search([
            ('category_id', '!=', self.treatment.uom_id.category_id.id),
        ], limit=1)
        self.assertTrue(mismatched_uom)

        with self.assertRaises(ValidationError):
            self._program('Mismatched UoM Program', self.trigger, [
                (self.treatment, mismatched_uom, 1.0),
            ])
