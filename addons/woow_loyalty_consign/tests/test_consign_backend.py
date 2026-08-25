from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user


class TestConsignBackendRouting(TransactionCase):
    """Manager-only backend adapters remain thin clients of the engine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Backend Consign Owner', 'company_id': cls.env.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Backend Consign Product', 'type': 'service', 'list_price': 99,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Backend Consign Program', 'program_type': 'consign',
            'active': True, 'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.issue = cls.env['loyalty.consign.engine']._issue(
            source=cls.partner, partner=cls.partner, program=cls.program,
            grants=[{'product': cls.product, 'quantity': 12}],
            idempotency_key='test:backend:issue',
        )
        cls.card = cls.issue.movement_ids.card_id
        cls.line = cls.issue.movement_ids.aggregate_line_id
        cls.sales = new_test_user(
            cls.env, login='consign_backend_sales',
            groups='sales_team.group_sale_salesman',
        )
        cls.pos_only = new_test_user(
            cls.env, login='consign_backend_pos_only', groups='base.group_user',
        )
        cls.manager = new_test_user(
            cls.env, login='consign_backend_manager',
            groups='woow_loyalty_consign.group_consign_manager',
        )
        cls.portal = new_test_user(
            cls.env, login='consign_backend_portal', groups='base.group_portal',
        )

    def _wizard(self, user=None, uuid='backend-redemption-uuid'):
        env = self.env(user=user) if user else self.env
        return env['consign.redeem.wizard'].create({
            'card_id': self.card.id,
            'service_note': 'treatment performed',
            'submission_uuid': uuid,
            'line_ids': [(0, 0, {
                'consign_line_id': self.line.id,
                'selected': True,
                'qty_to_redeem': 2,
                'note': 'exact treatment',
            })],
        })

    def test_only_consign_manager_can_create_manual_commands(self):
        for user in (self.sales, self.pos_only, self.portal):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self._wizard(user=user, uuid=f'blocked-{user.id}')
        wizard = self._wizard(user=self.manager)
        self.assertTrue(wizard.exists())

    def test_manager_wizard_captures_once_and_replays_exact_document(self):
        wizard = self._wizard(user=self.manager)
        first = wizard.action_confirm()
        second = wizard.action_confirm()
        document = self.env['loyalty.consign.redemption'].browse(first['res_id'])
        self.assertEqual(first['res_id'], second['res_id'])
        self.assertEqual(document.state, 'done')
        self.assertTrue(document.authorization_operation_id)
        self.assertTrue(document.capture_operation_id)
        self.assertEqual(document.capture_operation_id.movement_ids.movement_type, 'redeem')
        self.assertEqual(self.line.qty_redeemed, 2)
        self.assertEqual(document.submission_uuid, wizard.submission_uuid)

    def test_service_note_and_submission_uuid_are_required_before_capture(self):
        manager_env = self.env(user=self.manager)
        wizard = manager_env['consign.redeem.wizard'].create({
            'card_id': self.card.id,
            'submission_uuid': 'missing-note',
            'line_ids': [(0, 0, {
                'consign_line_id': self.line.id, 'selected': True, 'qty_to_redeem': 1,
            })],
        })
        with self.assertRaisesRegex(ValidationError, 'service note'):
            wizard.action_confirm()
        document = manager_env['loyalty.consign.redemption'].create({
            'card_id': self.card.id, 'service_note': 'document note',
        })
        with self.assertRaisesRegex(ValidationError, 'submission UUID'):
            document.action_done()

    def test_done_document_and_lines_are_immutable(self):
        wizard = self._wizard(user=self.manager, uuid='immutable-document-uuid')
        document = self.env['loyalty.consign.redemption'].browse(
            wizard.action_confirm()['res_id']
        )
        manager_document = document.with_user(self.manager)
        with self.assertRaises(ValidationError):
            manager_document.write({'service_note': 'altered'})
        with self.assertRaises(ValidationError):
            manager_document.line_ids.write({'qty_redeemed': 1})
        with self.assertRaises(ValidationError):
            manager_document.unlink()

    def test_direct_ledger_and_journal_mutation_is_denied(self):
        sales_env = self.env(user=self.sales)
        with self.assertRaises(ValidationError):
            sales_env['loyalty.consign.movement'].create({})
        with self.assertRaises(ValidationError):
            sales_env['loyalty.consign.operation'].create({})
        with self.assertRaises(ValidationError):
            sales_env['loyalty.consign.hold'].create({})

    def test_manager_adjustment_is_signed_reasoned_and_idempotent(self):
        manager_env = self.env(user=self.manager)
        wizard = manager_env['consign.adjust.wizard'].create({
            'card_id': self.card.id, 'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id, 'quantity': 3,
            'reason': 'inventory correction', 'submission_uuid': 'adjust-in-uuid',
        })
        first = wizard.action_confirm()
        second = wizard.action_confirm()
        operation = manager_env['loyalty.consign.operation'].browse(first['res_id'])
        self.assertEqual(first['res_id'], second['res_id'])
        self.assertEqual(operation.movement_ids.movement_type, 'adjustment_in')
        self.assertEqual(operation.movement_ids.quantity, 3)
        self.assertEqual(self.line.qty_available, 15)
        # A browser retry may recreate the transient wizard.  The stable UUID
        # must still replay the original command rather than append again.
        retry = manager_env['consign.adjust.wizard'].create({
            'card_id': self.card.id, 'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id, 'quantity': 3,
            'reason': 'inventory correction', 'submission_uuid': 'adjust-in-uuid',
        })
        self.assertEqual(retry.action_confirm()['res_id'], operation.id)
        self.assertEqual(self.line.qty_available, 15)

        out = manager_env['consign.adjust.wizard'].create({
            'card_id': self.card.id, 'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id, 'quantity': -2,
            'reason': 'stock correction', 'submission_uuid': 'adjust-out-uuid',
        })
        operation = manager_env['loyalty.consign.operation'].browse(out.action_confirm()['res_id'])
        self.assertEqual(operation.movement_ids.movement_type, 'adjustment_out')
        self.assertEqual(self.line.qty_available, 13)

    def test_adjustment_requires_reason_and_available_outgoing_capacity(self):
        manager_env = self.env(user=self.manager)
        missing_reason = manager_env['consign.adjust.wizard'].create({
            'card_id': self.card.id, 'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id, 'quantity': 1,
            'submission_uuid': 'adjust-missing-reason',
        })
        with self.assertRaisesRegex(ValidationError, 'reason'):
            missing_reason.action_confirm()
        insufficient = manager_env['consign.adjust.wizard'].create({
            'card_id': self.card.id, 'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id, 'quantity': -99,
            'reason': 'overdraw check', 'submission_uuid': 'adjust-overdraw',
        })
        with self.assertRaises(ValidationError):
            insufficient.action_confirm()

    def test_company_record_rules_reject_foreign_adjustment(self):
        foreign_company = self.env['res.company'].create({'name': 'Backend Foreign'})
        foreign_partner = self.env['res.partner'].sudo().create({
            'name': 'Backend Foreign Owner', 'company_id': foreign_company.id,
        })
        foreign_program = self.env['loyalty.program'].sudo().create({
            'name': 'Backend Foreign Program', 'program_type': 'consign', 'active': True,
            'company_id': foreign_company.id, 'currency_id': foreign_company.currency_id.id,
        })
        foreign_issue = self.env['loyalty.consign.engine']._issue(
            source=foreign_partner, partner=foreign_partner, program=foreign_program,
            grants=[{'product': self.product, 'quantity': 2}],
            idempotency_key='test:backend:foreign-issue',
        )
        foreign_card = foreign_issue.movement_ids.card_id
        foreign_document = self.env['loyalty.consign.redemption'].create({
            'card_id': foreign_card.id, 'service_note': 'foreign audit document',
        })
        self.assertFalse(self.env['loyalty.consign.redemption'].with_user(
            self.manager
        ).search([('id', '=', foreign_document.id)]))
        wizard = self.env(user=self.manager)['consign.adjust.wizard'].create({
            'card_id': foreign_card.id,
            'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
            'quantity': 1, 'reason': 'foreign', 'submission_uuid': 'foreign-adjust',
        })
        with self.assertRaises(ValidationError):
            wizard.action_confirm()
