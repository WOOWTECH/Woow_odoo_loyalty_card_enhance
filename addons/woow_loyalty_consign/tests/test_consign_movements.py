import inspect

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.woow_loyalty_consign.hooks import backfill_consign_movements
from odoo.addons.woow_loyalty_consign.models.loyalty_consign_movement import (
    LoyaltyConsignMovement,
)
from odoo.addons.woow_loyalty_consign.models.loyalty_consign_operation import (
    LoyaltyConsignOperation,
)


class TestConsignMovements(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Ledger Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Ledger Treatment', 'type': 'service', 'list_price': 120,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Ledger Program',
            'program_type': 'consign',
            'active': True,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.card = cls.env['loyalty.card'].create({
            'program_id': cls.program.id,
            'partner_id': cls.partner.id,
            'points': 0,
        })

    def _line(self, quantity=5, **extra):
        return self.env['loyalty.consign.line'].create({
            'card_id': self.card.id,
            'product_id': self.product.id,
            'qty_deposited': quantity,
            'unit_price': self.product.list_price,
            **extra,
        })

    def test_direct_line_appends_one_snapshot_issue(self):
        line = self._line(product_desc=False)
        movement = line.movement_ids

        self.assertEqual(len(movement), 1)
        self.assertEqual(movement.movement_type, 'issue')
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.product_id, self.product)
        self.assertEqual(movement.product_uom_id, self.product.uom_id)
        self.assertEqual(movement.partner_id, self.partner)
        self.assertEqual(movement.company_id, self.env.company)
        self.assertEqual(movement.product_desc_snapshot, self.product.display_name)
        self.assertEqual(movement.source_model, 'loyalty.consign.line')
        self.assertEqual(movement.source_name, self.product.display_name)
        self.assertEqual(movement.operation_id.source_name, self.product.display_name)
        self.assertEqual(movement.operation_id.state, 'done')
        self.assertEqual(line.qty_issued, 5)
        self.assertEqual(line.qty_available, line.qty_remaining)

    def test_manual_additions_keep_independent_lines_and_issue_facts(self):
        self.assertFalse(hasattr(self.card, 'consign_add_line'))
        first = self.card._consign_add_line(self.product, 2, 120)
        second = self.card._consign_add_line(self.product, 3, 120)

        self.assertNotEqual(first, second)
        lines = first | second
        movements = lines.mapped('movement_ids')
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(movements), 2)
        self.assertEqual(set(movements.mapped('movement_type')), {'issue'})
        self.assertEqual(sum(lines.mapped('qty_deposited')), 5)
        self.assertEqual(sum(movements.mapped('quantity')), 5)
        self.assertEqual(self.card.consign_total_remaining_qty, 5)
        self.assertEqual(self.card.consign_total_remaining_value, 600)

    def test_replay_returns_same_movement_and_conflict_is_rejected(self):
        line = self._line()
        original = line.movement_ids
        replay = self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='issue',
            quantity=5,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=line.id,
            source_name=line.display_name,
            idempotency_key=f'consign:legacy-line:v1:{line.id}:issue',
        )
        self.assertEqual(replay, original)
        self.assertEqual(len(line.movement_ids), 1)

        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.movement']._append_movement(
                aggregate_line=line,
                movement_type='issue',
                quantity=4,
                source_channel='manual',
                source_model='loyalty.consign.line',
                source_res_id=line.id,
                source_name=line.display_name,
                idempotency_key=f'consign:legacy-line:v1:{line.id}:issue',
            )

    def test_redemption_and_partial_cancellation_append_shadow_facts(self):
        line = self._line(quantity=5)
        redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': self.card.id,
            'line_ids': [(0, 0, {
                'consign_line_id': line.id,
                'qty_redeemed': 2,
            })],
        })
        redemption.action_done()
        self.assertEqual(
            line.movement_ids.filtered(lambda m: m.movement_type == 'redeem').quantity,
            2,
        )

        line.action_cancel()
        reversal = line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue_reversal'
        )
        self.assertEqual(sum(reversal.mapped('quantity')), 3)
        self.assertEqual(reversal.original_movement_id.movement_type, 'issue')
        self.assertEqual(line.state, 'cancelled')
        self.assertEqual(line.qty_available, 0)

    def test_posted_line_snapshot_writes_are_rejected_and_cancel_still_succeeds(self):
        line = self._line(quantity=2)
        other_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })
        protected_writes = [
            {'card_id': other_card.id},
            {'is_cancelled': True},
            {'sale_line_id': False},
            {'date_deposited': fields.Date.add(line.date_deposited, days=1)},
            {'lot_id': False},
            {'storage_note': 'Different shelf'},
        ]
        for vals in protected_writes:
            with self.subTest(field=next(iter(vals))):
                with self.assertRaises(ValidationError):
                    line.sudo().write(vals)

        self.assertIsNone(line.action_cancel())
        self.assertTrue(line.is_cancelled)
        self.assertEqual(line.state, 'cancelled')
        self.assertEqual(line.qty_available, 0)
        with self.assertRaises(ValidationError):
            line.sudo().write({'is_cancelled': False})

    def test_zero_non_consign_and_uom_mismatch_are_rejected(self):
        with self.assertRaises(ValidationError):
            self._line(quantity=0)

        ordinary_program = self.env['loyalty.program'].create({
            'name': 'Ordinary Loyalty',
            'program_type': 'loyalty',
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        ordinary_card = self.env['loyalty.card'].create({
            'program_id': ordinary_program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.line'].create({
                'card_id': ordinary_card.id,
                'product_id': self.product.id,
                'qty_deposited': 1,
            })

        mismatched_uom = self.env['uom.uom'].search([
            ('category_id', '!=', self.product.uom_id.category_id.id),
        ], limit=1)
        with self.assertRaises(ValidationError):
            self._line(product_uom_id=mismatched_uom.id)

    def test_operation_rejects_partner_company_mismatch(self):
        other_company = self.env['res.company'].create({'name': 'Other Ledger Company'})
        other_partner = self.env['res.partner'].create({
            'name': 'Other Company Customer',
            'company_id': other_company.id,
        })
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.operation']._open_command(
                operation_type='issue',
                company=self.env.company,
                partner=other_partner,
                source_model='res.partner',
                source_res_id=other_partner.id,
                source_name=other_partner.display_name,
                idempotency_key=f'test:company-mismatch:{other_partner.id}',
                payload={'quantity': 1},
            )

    def test_reversal_dimensions_and_quantity_are_validated(self):
        line = self._line(quantity=2)
        issue = line.movement_ids
        reversal = self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='issue_reversal',
            quantity=1,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=line.id,
            source_name=line.display_name,
            idempotency_key=f'test:reverse:{line.id}:1',
            original_movement=issue,
        )
        self.assertEqual(reversal.original_movement_id, issue)

        rejected_key = f'test:reverse:{line.id}:2'
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self.env['loyalty.consign.movement']._append_movement(
                    aggregate_line=line,
                    movement_type='issue_reversal',
                    quantity=2,
                    source_channel='manual',
                    source_model='loyalty.consign.line',
                    source_res_id=line.id,
                    source_name=line.display_name,
                    idempotency_key=rejected_key,
                    original_movement=issue,
                )
        self.assertFalse(self.env['loyalty.consign.operation'].search([
            ('company_id', '=', self.env.company.id),
            ('idempotency_key', '=', rejected_key),
        ]))

    def test_full_quantity_reversal_exact_replay_precedes_linked_cap(self):
        line = self._line(quantity=2)
        issue = line.movement_ids
        key = f'test:full-reversal:{line.id}'
        movement_model = self.env['loyalty.consign.movement']
        values = {
            'aggregate_line': line,
            'movement_type': 'issue_reversal',
            'quantity': 2,
            'source_channel': 'manual',
            'source_model': 'loyalty.consign.line',
            'source_res_id': line.id,
            'source_name': line.display_name,
            'idempotency_key': key,
            'original_movement': issue,
        }

        reversal = movement_model._append_movement(**values)
        replay = movement_model._append_movement(**values)

        self.assertEqual(replay, reversal)
        self.assertEqual(reversal.quantity, issue.quantity)
        self.assertEqual(len(line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue_reversal'
        )), 1)

        rejected_key = f'{key}:distinct'
        with self.assertRaisesRegex(
            ValidationError, 'cannot exceed its original movement'
        ):
            with self.env.cr.savepoint():
                movement_model._append_movement(**{
                    **values,
                    'quantity': 1,
                    'idempotency_key': rejected_key,
                })
        self.assertFalse(self.env['loyalty.consign.operation'].search([
            ('company_id', '=', self.env.company.id),
            ('idempotency_key', '=', rejected_key),
        ]))

    def test_durable_token_sql_and_lock_order_are_explicit(self):
        company_token_source = inspect.getsource(
            LoyaltyConsignOperation._touch_company_serialization_token
        )
        self.assertIn('UPDATE res_company', company_token_source)
        self.assertIn('SET write_date = write_date', company_token_source)
        self.assertIn('RETURNING id', company_token_source)
        self.assertIn('fetchone()', company_token_source)

        open_source = inspect.getsource(LoyaltyConsignOperation._open_command)
        advisory_index = open_source.index('_lock_idempotency_key')
        company_token_index = open_source.index('_touch_company_serialization_token')
        operation_search_index = open_source.index('operation = self.sudo().search')
        self.assertLess(advisory_index, company_token_index)
        self.assertLess(company_token_index, operation_search_index)

        original_token_source = inspect.getsource(
            LoyaltyConsignMovement._lock_original_operation_token
        )
        self.assertIn('UPDATE loyalty_consign_operation', original_token_source)
        self.assertIn('SET write_date = write_date', original_token_source)
        self.assertIn('RETURNING id', original_token_source)
        self.assertIn('fetchone()', original_token_source)

        append_source = inspect.getsource(LoyaltyConsignMovement._append_movement)
        open_index = append_source.index("._open_command(")
        replay_index = append_source.index('if replay and existing:')
        original_token_index = append_source.index('_lock_original_operation_token')
        linked_search_index = append_source.index('linked_quantity = sum')
        movement_create_index = append_source.index('}).sudo().create({')
        self.assertLess(open_index, replay_index)
        self.assertLess(replay_index, original_token_index)
        self.assertLess(original_token_index, linked_search_index)
        self.assertLess(linked_search_index, movement_create_index)
        self.assertNotIn('except SerializationFailure', append_source)

    def _create_active_hold_allocation(self, line):
        issue = line.movement_ids.filtered(lambda movement: movement.movement_type == 'issue')
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': issue.operation_id.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': 'loyalty.consign.line',
            'source_res_id': line.id,
            'source_name': False,
        })
        self.assertEqual(hold.source_name, issue.operation_id.source_name)
        allocation = self.env['loyalty.consign.hold.allocation']._create_from_engine({
            'hold_id': hold.id,
            'aggregate_line_id': line.id,
            'issue_movement_id': issue.id,
            'quantity': 1,
        })
        return hold, allocation

    def test_hold_models_reject_public_crud_and_allow_private_engine_seams(self):
        line = self._line(quantity=3)
        issue = line.movement_ids
        hold_vals = {
            'operation_id': issue.operation_id.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': 'loyalty.consign.line',
            'source_res_id': line.id,
            'source_name': 'Explicit Hold Snapshot',
        }
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.hold'].sudo().create(hold_vals)
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.hold']._create_from_engine({
                **hold_vals,
                'source_res_id': line.id + 1,
            })

        hold = self.env['loyalty.consign.hold']._create_from_engine(hold_vals)
        self.assertEqual(hold.source_name, 'Explicit Hold Snapshot')
        self.assertEqual(hold.company_id, issue.operation_id.company_id)
        self.assertEqual(hold.partner_id, issue.operation_id.partner_id)
        self.assertEqual(hold.source_model, issue.operation_id.source_model)
        self.assertEqual(hold.source_res_id, issue.operation_id.source_res_id)
        with self.assertRaises(ValidationError):
            hold.sudo().write({'state': 'released'})
        with self.assertRaises(ValidationError):
            hold.sudo().copy()
        with self.assertRaises(ValidationError):
            hold.sudo().create(hold_vals)
        hold._write_from_engine({
            'state': 'released',
            'released_at': fields.Datetime.now(),
        })
        self.assertEqual(hold.state, 'released')

        allocation_vals = {
            'hold_id': hold.id,
            'aggregate_line_id': line.id,
            'issue_movement_id': issue.id,
            'quantity': 1,
        }
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.hold.allocation'].sudo().create(allocation_vals)
        allocation = self.env[
            'loyalty.consign.hold.allocation'
        ]._create_from_engine(allocation_vals)
        with self.assertRaises(ValidationError):
            allocation.sudo().write({'quantity': 2})
        with self.assertRaises(ValidationError):
            allocation.sudo().copy()
        with self.assertRaises(ValidationError):
            allocation.sudo().create(allocation_vals)
        allocation._write_from_engine({'quantity': 1})
        with self.assertRaises(ValidationError):
            allocation.sudo().unlink()
        with self.assertRaises(ValidationError):
            hold.sudo().unlink()

    def test_private_create_seams_do_not_leak_tokens_to_returned_records(self):
        aggregate_line = self._line(quantity=2)
        movement = self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=aggregate_line,
            movement_type='adjustment_in',
            quantity=1,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=aggregate_line.id,
            source_name=aggregate_line.display_name,
            idempotency_key=f'test:clean-movement-context:{aggregate_line.id}',
        )
        with self.assertRaises(ValidationError):
            movement.sudo().create({})
        with self.assertRaises(ValidationError):
            movement.sudo().copy({
                'idempotency_key': f'test:movement-copy:{movement.id}',
            })

        line_vals = {
            'card_id': self.card.id,
            'product_id': self.product.id,
            'qty_deposited': 1,
            'unit_price': self.product.list_price,
            'product_desc': 'Specific movement source',
        }
        specific_line = self.env[
            'loyalty.consign.line'
        ]._create_for_specific_movement(line_vals)
        self.assertFalse(specific_line.movement_ids)

        copied_line = specific_line.sudo().copy({
            'product_desc': 'Copied specific movement source',
        })
        created_line = specific_line.sudo().create({
            **line_vals,
            'product_desc': 'Created from returned recordset',
        })
        self.assertEqual(len(copied_line.movement_ids), 1)
        self.assertEqual(copied_line.movement_ids.movement_type, 'issue')
        self.assertEqual(len(created_line.movement_ids), 1)
        self.assertEqual(created_line.movement_ids.movement_type, 'issue')

    def test_hold_allocation_rejects_same_company_different_owner_issue(self):
        held_line = self._line(quantity=2)
        held_issue = held_line.movement_ids
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': held_issue.operation_id.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': 'loyalty.consign.line',
            'source_res_id': held_line.id,
            'source_name': False,
        })

        other_partner = self.env['res.partner'].create({'name': 'Other Ledger Owner'})
        other_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': other_partner.id,
            'points': 0,
        })
        other_line = self.env['loyalty.consign.line'].create({
            'card_id': other_card.id,
            'product_id': self.product.id,
            'qty_deposited': 1,
            'unit_price': self.product.list_price,
        })
        with self.assertRaises(ValidationError):
            self.env[
                'loyalty.consign.hold.allocation'
            ]._create_from_engine({
                'hold_id': hold.id,
                'aggregate_line_id': other_line.id,
                'issue_movement_id': other_line.movement_ids.id,
                'quantity': 1,
            })

    def test_active_hold_blocks_cancellation_and_issue_reversal(self):
        line = self._line(quantity=3)
        _hold, allocation = self._create_active_hold_allocation(line)
        self.assertEqual(allocation.hold_id.state, 'active')

        rejected_key = f'test:held-reversal:{line.id}'
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self.env['loyalty.consign.movement']._append_movement(
                    aggregate_line=line,
                    movement_type='issue_reversal',
                    quantity=1,
                    source_channel='manual',
                    source_model='loyalty.consign.line',
                    source_res_id=line.id,
                    source_name=line.display_name,
                    idempotency_key=rejected_key,
                    original_movement=line.movement_ids,
                )
        self.assertFalse(self.env['loyalty.consign.operation'].search([
            ('company_id', '=', self.env.company.id),
            ('idempotency_key', '=', rejected_key),
        ]))
        with self.assertRaises(ValidationError):
            line.action_cancel()
        self.assertEqual(line.state, 'active')

    def test_portal_movement_access_is_exact_owner_read_only(self):
        owner = new_test_user(
            self.env, login='consign-ledger-owner', groups='base.group_portal',
        )
        other = new_test_user(
            self.env, login='consign-ledger-other', groups='base.group_portal',
        )
        owner_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': owner.partner_id.id,
            'points': 0,
        })
        owner_line = self.env['loyalty.consign.line'].create({
            'card_id': owner_card.id,
            'product_id': self.product.id,
            'qty_deposited': 1,
            'unit_price': self.product.list_price,
        })
        movement = owner_line.movement_ids

        self.assertEqual(
            self.env['loyalty.consign.line'].with_user(owner).search([
                ('id', '=', owner_line.id),
            ]),
            owner_line,
        )
        self.assertEqual(
            self.env['loyalty.consign.movement'].with_user(owner).search([
                ('id', '=', movement.id),
            ]),
            movement,
        )
        self.assertFalse(
            self.env['loyalty.consign.movement'].with_user(other).search([
                ('id', '=', movement.id),
            ])
        )
        with self.assertRaises(AccessError):
            movement.with_user(owner).check_access('write')

        movement_count = self.env['loyalty.consign.movement'].sudo().search_count([])
        with self.assertRaises(AccessError):
            owner_line.with_user(owner).action_cancel()
        self.assertEqual(
            self.env['loyalty.consign.movement'].sudo().search_count([]),
            movement_count,
        )
        self.assertEqual(owner_line.state, 'active')
        self.assertFalse(owner_line.is_cancelled)

    def test_only_sale_manager_or_superuser_can_cancel(self):
        salesperson = new_test_user(
            self.env,
            login='consign-cancel-salesperson',
            groups='sales_team.group_sale_salesman',
        )
        manager = new_test_user(
            self.env,
            login='consign-cancel-manager',
            groups='sales_team.group_sale_manager',
        )
        denied_line = self._line(quantity=1)
        manager_line = self._line(quantity=1)
        superuser_line = self._line(quantity=1)
        before_count = self.env['loyalty.consign.movement'].sudo().search_count([])

        with self.assertRaisesRegex(AccessError, 'Only Sales managers'):
            denied_line.with_user(salesperson).action_cancel()
        self.assertEqual(
            self.env['loyalty.consign.movement'].sudo().search_count([]),
            before_count,
        )
        self.assertEqual(denied_line.state, 'active')
        self.assertFalse(denied_line.is_cancelled)

        self.assertIsNone(manager_line.with_user(manager).action_cancel())
        manager_line.invalidate_recordset()
        self.assertEqual(manager_line.state, 'cancelled')
        self.assertTrue(manager_line.is_cancelled)

        self.assertIsNone(superuser_line.sudo().action_cancel())
        superuser_line.invalidate_recordset()
        self.assertEqual(superuser_line.state, 'cancelled')
        self.assertTrue(superuser_line.is_cancelled)
        self.assertEqual(
            self.env['loyalty.consign.movement'].sudo().search_count([]),
            before_count + 2,
        )

    def test_orm_and_sql_movement_mutation_are_blocked_even_with_sudo(self):
        movement = self._line().movement_ids
        with self.assertRaises(ValidationError):
            movement.sudo().write({'quantity': 9})
        with self.assertRaises(ValidationError):
            movement.sudo().unlink()
        with self.assertRaises(ValidationError):
            self.env['loyalty.consign.movement'].sudo().create({})

        self.env.flush_all()
        with self.assertRaises(Exception), self.env.cr.savepoint():
            self.env.cr.execute(
                'UPDATE loyalty_consign_movement SET quantity = quantity + 1 WHERE id = %s',
                (movement.id,),
            )
        with self.assertRaises(Exception), self.env.cr.savepoint():
            self.env.cr.execute(
                'DELETE FROM loyalty_consign_movement WHERE id = %s',
                (movement.id,),
            )

    def test_source_action_and_missing_source_are_safe(self):
        line = self._line()
        movement = line.movement_ids
        action = movement.action_open_source()
        self.assertEqual(action['res_model'], 'loyalty.consign.line')
        self.assertEqual(action['res_id'], line.id)

        # Immutable source metadata can only be simulated by a new command.
        missing = self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='adjustment_in',
            quantity=1,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=2_147_000_000,
            source_name='Unavailable',
            idempotency_key=f'test:missing-source:{line.id}',
        )
        self.assertEqual(missing.action_open_source()['tag'], 'display_notification')

    def test_backfill_is_idempotent_and_net_matches_legacy_facts(self):
        line = self._line(quantity=4)
        redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': self.card.id,
            'line_ids': [(0, 0, {
                'consign_line_id': line.id,
                'qty_redeemed': 1,
            })],
        })
        redemption.action_done()
        before = self.env['loyalty.consign.movement'].search_count([])

        backfill_consign_movements(self.env)
        backfill_consign_movements(self.env)

        self.assertEqual(self.env['loyalty.consign.movement'].search_count([]), before)
        self.assertEqual(line.qty_available, line.qty_remaining)
