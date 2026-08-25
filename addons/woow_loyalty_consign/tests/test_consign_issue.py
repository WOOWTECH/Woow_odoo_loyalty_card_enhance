import inspect
import os
from pathlib import Path

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.woow_loyalty_consign.models.loyalty_consign_engine import (
    LoyaltyConsignEngine,
)


class TestConsignIssue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Issue Customer', 'company_id': cls.env.company.id,
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Other Issue Customer', 'company_id': cls.env.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Issue Treatment', 'type': 'service', 'list_price': 125,
        })
        cls.second_product = cls.env['product.product'].create({
            'name': 'Issue Aftercare', 'type': 'service', 'list_price': 30,
        })
        cls.program = cls._program('Issue Program')

    @classmethod
    def _program(cls, name):
        return cls.env['loyalty.program'].create({
            'name': name,
            'program_type': 'consign',
            'active': True,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })

    def _issue(self, key, grants=None, partner=None, program=None, source=None):
        return self.env['loyalty.consign.engine']._issue(
            source=source or self.partner,
            partner=partner or self.partner,
            program=program or self.program,
            grants=grants or [{
                'product': self.product,
                'quantity': 2,
                'source_channel': 'manual',
                'provenance_key': 'default',
            }],
            idempotency_key=key,
        )

    def test_exact_replay_returns_completed_operation(self):
        operation = self._issue('test:issue:replay')
        replay = self._issue('test:issue:replay')

        self.assertEqual(replay, operation)
        self.assertEqual(operation.state, 'done')
        self.assertEqual(len(operation.movement_ids), 1)
        self.assertEqual(operation.result_json['movement_ids'], operation.movement_ids.ids)

    def test_payload_mismatch_same_key_is_rejected(self):
        self._issue('test:issue:mismatch')
        with self.assertRaisesRegex(ValidationError, 'different payload'):
            self._issue('test:issue:mismatch', [{
                'product': self.product,
                'quantity': 3,
                'source_channel': 'manual',
                'provenance_key': 'default',
            }])

    def test_pure_validation_precedes_journal_insertion(self):
        before = self.env['loyalty.consign.operation'].sudo().search_count([])
        with self.assertRaises(ValidationError):
            self._issue('test:issue:invalid-before-journal', [{
                'product': self.product,
                'quantity': self.product.uom_id.rounding / 10,
            }])
        self.assertEqual(
            self.env['loyalty.consign.operation'].sudo().search_count([]), before,
        )

    def test_one_operation_has_independent_issue_movements_and_one_projection(self):
        operation = self._issue('test:issue:multi-fact', [
            {
                'product': self.product, 'quantity': 2,
                'source_channel': 'manual', 'provenance_key': 'grant-a',
            },
            {
                'product': self.product, 'quantity': 3,
                'source_channel': 'manual', 'provenance_key': 'grant-b',
            },
        ])
        card = self.env['loyalty.card'].browse(operation.result_json['card_id'])
        line = card.consign_line_ids

        self.assertEqual(len(line), 1)
        self.assertEqual(len(operation.movement_ids), 2)
        self.assertEqual(len(operation.movement_ids.mapped('operation_id')), 1)
        self.assertEqual(sum(operation.movement_ids.mapped('quantity')), 5)
        self.assertEqual(line.qty_issued, 5)
        self.assertEqual(line.qty_remaining, 5)
        self.assertEqual(line.qty_available, 5)
        self.assertTrue(line._assert_projection_consistent())

    def test_multiple_products_create_multiple_exact_projections(self):
        operation = self._issue('test:issue:multi-product', [
            {'product': self.product, 'quantity': 1},
            {'product': self.second_product, 'quantity': 4},
        ])
        card = self.env['loyalty.card'].browse(operation.result_json['card_id'])
        self.assertEqual(set(card.consign_line_ids.product_id.ids), {
            self.product.id, self.second_product.id,
        })
        self.assertEqual(len(card.consign_line_ids), 2)

    def test_uom_is_normalized_and_rounded_to_product_uom(self):
        dozen = self.env['uom.uom'].create({
            'name': 'Issue Dozen',
            'category_id': self.product.uom_id.category_id.id,
            'uom_type': 'bigger',
            'factor_inv': 12,
            'rounding': 0.01,
        })
        operation = self._issue('test:issue:uom', [{
            'product': self.product,
            'product_uom': dozen,
            'quantity': 0.5,
        }])
        movement = operation.movement_ids
        self.assertEqual(movement.product_uom_id, self.product.uom_id)
        self.assertEqual(movement.quantity, 6)

    def test_source_owner_and_program_validation(self):
        order = self.env['sale.order'].create({'partner_id': self.other_partner.id})
        before = self.env['loyalty.consign.operation'].sudo().search_count([])
        with self.assertRaisesRegex(ValidationError, 'source customer'):
            self._issue('test:issue:wrong-owner', source=order)
        self.assertEqual(
            self.env['loyalty.consign.operation'].sudo().search_count([]), before,
        )
        owned_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Owned source',
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': 1,
            })],
        })
        foreign_line = self.env['sale.order'].create({
            'partner_id': self.other_partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Foreign source',
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': 1,
            })],
        }).order_line
        with self.assertRaisesRegex(ValidationError, 'exact issue customer'):
            self._issue('test:issue:wrong-source-line-owner', [{
                'product': self.product,
                'quantity': 1,
                'source_line': foreign_line,
            }], source=owned_order)
        self.program.active = False
        with self.assertRaisesRegex(ValidationError, 'active consignment program'):
            self._issue('test:issue:inactive-program')

    def test_company_mismatches_are_rejected_before_journal(self):
        other_company = self.env['res.company'].create({'name': 'Issue Other Company'})
        other_program = self.env['loyalty.program'].create({
            'name': 'Other Company Issue Program',
            'program_type': 'consign',
            'active': True,
            'company_id': other_company.id,
            'currency_id': other_company.currency_id.id,
        })
        other_product = self.env['product.product'].sudo().create({
            'name': 'Other Company Entitlement',
            'type': 'service',
            'company_id': other_company.id,
        })
        before = self.env['loyalty.consign.operation'].sudo().search_count([])
        with self.assertRaisesRegex(ValidationError, 'exact source company'):
            self._issue('test:issue:wrong-program-company', program=other_program)
        with self.assertRaisesRegex(ValidationError, 'product belongs'):
            self._issue('test:issue:wrong-product-company', [{
                'product': other_product, 'quantity': 1,
            }])
        foreign_source = self.env['res.partner'].create({
            'name': 'Foreign grant source', 'company_id': other_company.id,
        })
        with self.assertRaisesRegex(ValidationError, 'exact operation company'):
            self._issue('test:issue:wrong-grant-source-company', [{
                'product': self.product,
                'quantity': 1,
                'source_line': foreign_source,
            }])
        global_program = self.env['loyalty.program'].create({
            'name': 'Rejected Global Issue Program',
            'program_type': 'consign',
            'active': True,
            'company_id': False,
            'currency_id': self.env.company.currency_id.id,
        })
        with self.assertRaisesRegex(ValidationError, 'exact source company'):
            self._issue('test:issue:global-program', program=global_program)
        self.assertEqual(
            self.env['loyalty.consign.operation'].sudo().search_count([]), before,
        )

    def test_distinct_issue_keys_reuse_automatic_card(self):
        first = self._issue('test:issue:card-reuse:first')
        second = self._issue('test:issue:card-reuse:second')
        self.assertEqual(first.result_json['card_id'], second.result_json['card_id'])
        self.assertEqual(self.env['loyalty.card'].search_count([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', self.partner.id),
            ('active', '=', True),
        ]), 1)

    def test_two_programs_create_separate_cards(self):
        second_program = self._program('Second Issue Program')
        first = self._issue('test:issue:program:first')
        second = self._issue('test:issue:program:second', program=second_program)
        self.assertNotEqual(first.result_json['card_id'], second.result_json['card_id'])

    def test_manual_compatibility_create_reconciles_same_projection(self):
        card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })
        first = self.env['loyalty.consign.line'].create({
            'card_id': card.id, 'product_id': self.product.id,
            'qty_deposited': 2, 'unit_price': 125,
        })
        second = self.env['loyalty.consign.line'].create({
            'card_id': card.id, 'product_id': self.product.id,
            'qty_deposited': 3, 'unit_price': 125,
        })
        self.assertEqual(first, second)
        self.assertEqual(len(first.movement_ids), 2)
        self.assertEqual(first.qty_deposited, 5)
        self.assertEqual(first.qty_available, 5)

    def test_active_hold_only_reduces_available(self):
        operation = self._issue('test:issue:active-hold')
        line = operation.movement_ids.aggregate_line_id
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': operation.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': operation.source_model,
            'source_res_id': operation.source_res_id,
            'source_name': operation.source_name,
        })
        self.env['loyalty.consign.hold.allocation']._create_from_engine({
            'hold_id': hold.id,
            'aggregate_line_id': line.id,
            'issue_movement_id': operation.movement_ids.id,
            'quantity': 1,
        })
        self.assertEqual(line.qty_remaining, 2)
        self.assertEqual(line.qty_on_hold, 1)
        self.assertEqual(line.qty_available, 1)

    def test_manager_repair_only_rewrites_projection(self):
        operation = self._issue('test:issue:repair')
        line = self.env['loyalty.consign.line'].browse(
            operation.result_json['projection_ids']
        )
        movement_ids = line.movement_ids.ids
        self.env.cr.execute(
            'UPDATE loyalty_consign_line SET qty_available = 999 WHERE id = %s',
            (line.id,),
        )
        line.invalidate_recordset()
        with self.assertRaisesRegex(ValidationError, 'inconsistent'):
            line._assert_projection_consistent()
        self.assertTrue(line.sudo().action_repair_projection())
        self.assertEqual(line.movement_ids.ids, movement_ids)
        self.assertTrue(line._assert_projection_consistent())

    def test_issue_source_provenance_is_on_movements_not_projection_duplicates(self):
        first_source = self.env['sale.order'].create({'partner_id': self.partner.id})
        second_source = self.env['sale.order'].create({'partner_id': self.partner.id})
        first = self._issue('test:issue:source:first', source=first_source)
        second = self._issue('test:issue:source:second', source=second_source)
        card = self.env['loyalty.card'].browse(first.result_json['card_id'])
        self.assertEqual(first.result_json['card_id'], second.result_json['card_id'])
        self.assertEqual(len(card.consign_line_ids), 1)
        self.assertEqual(set(card.consign_movement_ids.mapped('source_res_id')), {
            first_source.id, second_source.id,
        })
        self.assertFalse(card.consign_line_ids.sale_line_id)

    def test_public_projection_create_is_manager_only_before_side_effects(self):
        salesperson = new_test_user(
            self.env, login='task4_projection_salesperson',
            groups='sales_team.group_sale_salesman',
            company_id=self.env.company.id,
        )
        portal = new_test_user(
            self.env, login='task4_projection_portal', groups='base.group_portal',
            company_id=self.env.company.id,
        )
        card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': self.partner.id,
            'points': 0,
        })
        before_movements = self.env['loyalty.consign.movement'].sudo().search_count([])
        before_operations = self.env['loyalty.consign.operation'].sudo().search_count([])
        vals = {
            'card_id': card.id, 'product_id': self.product.id,
            'qty_deposited': 1,
        }
        for user in (salesperson, portal):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.env['loyalty.consign.line'].with_user(user).create(vals)
        self.assertEqual(
            self.env['loyalty.consign.movement'].sudo().search_count([]),
            before_movements,
        )
        self.assertEqual(
            self.env['loyalty.consign.operation'].sudo().search_count([]),
            before_operations,
        )
        manager = new_test_user(
            self.env, login='task4_projection_manager',
            groups='woow_loyalty_consign.group_consign_manager',
            company_id=self.env.company.id,
        )
        line = self.env['loyalty.consign.line'].with_user(manager).create(vals)
        self.assertEqual(line.qty_available, 1)
        with self.assertRaises(ValidationError):
            line.with_user(manager).write({'storage_note': 'forbidden'})

    def test_portal_card_acl_is_exact_owner(self):
        owner = new_test_user(
            self.env, login='task4_card_owner', groups='base.group_portal',
            company_id=self.env.company.id,
        )
        other = new_test_user(
            self.env, login='task4_card_other', groups='base.group_portal',
            company_id=self.env.company.id,
        )
        owner_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': owner.partner_id.id,
            'points': 0,
        })
        other_card = self.env['loyalty.card'].create({
            'program_id': self.program.id,
            'partner_id': other.partner_id.id,
            'points': 0,
        })
        self.assertEqual(
            self.env['loyalty.card'].with_user(owner).search([
                ('id', '=', owner_card.id),
            ]), owner_card,
        )
        self.assertFalse(self.env['loyalty.card'].with_user(owner).search([
            ('id', '=', other_card.id),
        ]))
        with self.assertRaises(AccessError):
            other_card.with_user(owner).check_access('read')

    def test_grant_aliases_and_server_owned_value(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Alias source',
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': 1,
            })],
        })
        operation = self._issue('test:issue:aliases', [{
            'product': self.product,
            'uom': self.product.uom_id,
            'quantity': 2,
            'source_line': order.order_line,
            'unit_value': 999999,
            'provenance_key': 'alias',
        }], source=order)
        movement = operation.movement_ids
        self.assertEqual(movement.source_model, 'sale.order.line')
        self.assertEqual(movement.source_res_id, order.order_line.id)
        self.assertEqual(movement.unit_value, self.product.list_price)

    def test_replay_identity_ignores_mutable_snapshots(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': 'Original source name',
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': 1,
            })],
        })
        grants = [{
            'product': self.product,
            'quantity': 2,
            'source_line': order.order_line,
            'provenance_key': 'rename-stable',
        }]
        operation = self._issue(
            'test:issue:rename-stable', grants, source=order,
        )
        self.product.write({'name': 'Renamed entitlement', 'list_price': 777})
        order.order_line.write({'name': 'Renamed source line'})
        replay = self._issue(
            'test:issue:rename-stable', grants, source=order,
        )
        self.assertEqual(replay, operation)
        self.assertEqual(len(operation.movement_ids), 1)
        self.assertEqual(operation.movement_ids.unit_value, 125)

    def test_ambiguous_lot_metadata_is_cleared_from_projection(self):
        lot = self.env['stock.lot'].create({
            'name': 'TASK4-LOT-A', 'product_id': self.product.id,
            'company_id': self.env.company.id,
        })
        operation = self._issue('test:issue:lot-ambiguity', [
            {
                'product': self.product, 'quantity': 1,
                'lot_snapshot': lot.name, 'provenance_key': 'with-lot',
            },
            {
                'product': self.product, 'quantity': 1,
                'provenance_key': 'without-lot',
            },
        ])
        self.assertFalse(operation.movement_ids.aggregate_line_id.lot_id)

    def test_hold_allocation_caps_issue_and_authoritative_availability(self):
        operation = self._issue('test:issue:hold-cap')
        line = operation.movement_ids.aggregate_line_id
        issue = operation.movement_ids

        def create_hold(suffix):
            return self.env['loyalty.consign.hold']._create_from_engine({
                'operation_id': operation.id,
                'company_id': self.env.company.id,
                'partner_id': self.partner.id,
                'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
                'source_model': operation.source_model,
                'source_res_id': operation.source_res_id,
                'source_name': f'Hold {suffix}',
            })

        first = self.env['loyalty.consign.hold.allocation']._create_from_engine({
            'hold_id': create_hold('first').id,
            'aggregate_line_id': line.id,
            'issue_movement_id': issue.id,
            'quantity': 2,
        })
        with self.assertRaisesRegex(ValidationError, 'cannot exceed'):
            self.env['loyalty.consign.hold.allocation']._create_from_engine({
                'hold_id': create_hold('excess').id,
                'aggregate_line_id': line.id,
                'issue_movement_id': issue.id,
                'quantity': 1,
            })
        with self.assertRaisesRegex(ValidationError, 'cannot exceed'):
            first._write_from_engine({'quantity': 3})
        self.assertEqual(first.quantity, 2)
        self.assertEqual(line.qty_available, 0)

    def test_reversed_issue_cannot_receive_hold_from_other_issue_availability(self):
        first = self._issue('test:issue:revoked-hold:first', [{
            'product': self.product, 'quantity': 1,
        }])
        second = self._issue('test:issue:revoked-hold:second', [{
            'product': self.product, 'quantity': 1,
        }])
        line = first.movement_ids.aggregate_line_id
        first_issue = first.movement_ids
        self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='issue_reversal',
            quantity=1,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=line.id,
            source_name='Revoke first issue',
            idempotency_key='test:issue:revoked-hold:reversal',
            original_movement=first_issue,
        )
        self.assertEqual(line.qty_available, 1)
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': second.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': second.source_model,
            'source_res_id': second.source_res_id,
            'source_name': 'Reversed issue Hold',
        })
        with self.assertRaisesRegex(ValidationError, 'unused issue'):
            self.env['loyalty.consign.hold.allocation']._create_from_engine({
                'hold_id': hold.id,
                'aggregate_line_id': line.id,
                'issue_movement_id': first_issue.id,
                'quantity': 1,
            })
        self.assertFalse(hold.allocation_line_ids)
        self.assertEqual(line.qty_available, 1)

    def test_consumed_issue_cannot_receive_hold_from_other_issue_availability(self):
        first = self._issue('test:issue:consumed-hold:first', [{
            'product': self.product, 'quantity': 1,
        }])
        second = self._issue('test:issue:consumed-hold:second', [{
            'product': self.product, 'quantity': 1,
        }])
        line = first.movement_ids.aggregate_line_id
        first_issue = first.movement_ids
        self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='redeem',
            quantity=1,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=line.id,
            source_name='Consume first issue',
            idempotency_key='test:issue:consumed-hold:redeem',
            original_movement=first_issue,
        )
        self.assertEqual(line.qty_available, 1)
        hold = self.env['loyalty.consign.hold']._create_from_engine({
            'operation_id': second.id,
            'company_id': self.env.company.id,
            'partner_id': self.partner.id,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), hours=1),
            'source_model': second.source_model,
            'source_res_id': second.source_res_id,
            'source_name': 'Consumed issue Hold',
        })
        with self.assertRaisesRegex(ValidationError, 'unused issue'):
            self.env['loyalty.consign.hold.allocation']._create_from_engine({
                'hold_id': hold.id,
                'aggregate_line_id': line.id,
                'issue_movement_id': first_issue.id,
                'quantity': 1,
            })
        self.assertFalse(hold.allocation_line_ids)
        self.assertEqual(line.qty_available, 1)

    def test_reversal_uses_original_issue_value_not_blended_projection(self):
        self.product.list_price = 100
        first = self._issue('test:issue:value:first', [{
            'product': self.product, 'quantity': 1,
        }])
        self.product.list_price = 200
        second = self._issue('test:issue:value:second', [{
            'product': self.product, 'quantity': 1,
        }])
        line = second.movement_ids.aggregate_line_id
        first_issue = first.movement_ids
        self.assertEqual(line.unit_price, 150)
        self.env['loyalty.consign.movement']._append_movement(
            aggregate_line=line,
            movement_type='issue_reversal',
            quantity=first_issue.quantity,
            source_channel='manual',
            source_model='loyalty.consign.line',
            source_res_id=line.id,
            source_name='Value reversal',
            idempotency_key='test:issue:value:reverse-first',
            original_movement=first_issue,
        )
        self.assertEqual(line.amount_remaining, 200)
        reversal = line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue_reversal'
        )
        self.assertEqual(reversal.unit_value, 100)
        self.assertEqual(reversal.value_delta, 100)
        self.assertEqual(line.card_id.consign_total_remaining_value, 200)
        before_operations = self.env['loyalty.consign.operation'].search_count([])
        with self.assertRaisesRegex(ValidationError, 'preserve its original unit value'):
            self.env['loyalty.consign.movement']._append_movement(
                aggregate_line=line,
                movement_type='issue_reversal',
                quantity=1,
                source_channel='manual',
                source_model='loyalty.consign.line',
                source_res_id=line.id,
                source_name='Forged value reversal',
                idempotency_key='test:issue:value:forged-reversal',
                original_movement=first_issue,
                unit_value=999,
            )
        self.assertEqual(
            self.env['loyalty.consign.operation'].search_count([]),
            before_operations,
        )

    def test_engine_has_no_remotely_callable_public_interface(self):
        own_methods = {
            name for name, value in LoyaltyConsignEngine.__dict__.items()
            if callable(value) and not name.startswith('_')
        }
        self.assertFalse(own_methods)

    def test_versioned_migration_contains_controlled_safety_gates(self):
        addon_root = Path(inspect.getfile(LoyaltyConsignEngine)).parents[1]
        source = (addon_root / 'migrations' / '18.0.5.0.0' / 'pre-migration.py').read_text()
        for required in (
            'ACCESS EXCLUSIVE', 'pg_constraint',
            'woow_consign_projection_full_map',
            'woow_consign_projection_duplicate_map',
            'loyalty_consign_projection_merge_run',
            'loyalty_consign_projection_merge_map', 'DROP TRIGGER IF EXISTS',
            'CREATE TRIGGER', 'dangling', 'movement quantity',
            'allocation quantity', 'redemption quantity',
            'loyalty_consign_projection_merge_dimension_audit',
            "source.source_model = 'loyalty.consign.line'",
            'loyalty_consign_refund_saga', 'convalidated',
            'projection unique constraint accepted a duplicate',
            'movement trigger accepted an update',
            'UNIQUE (card_id, product_id, product_uom_id)',
        ):
            self.assertIn(required, source)
        probe = addon_root / 'tests' / 'probes' / 'task4_concurrency_probe.py'
        self.assertTrue(probe.exists())
        self.assertTrue(os.access(probe, os.X_OK))
        probe_source = probe.read_text()
        self.assertIn('SerializationFailure', probe_source)
        self.assertIn('operation.token', probe_source)
        self.assertIn('ABSENT_CARD_DISTINCT_KEYS=PASS', probe_source)
        self.assertIn('PROJECTION_REPAIR=PASS', probe_source)
        self.assertIn('refuses a non-test database', probe_source)
