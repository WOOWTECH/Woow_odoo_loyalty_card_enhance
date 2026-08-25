from odoo.tests.common import TransactionCase


class TestConsignRendering(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({
            'name': 'Rendered Recipient',
            'email': 'recipient@example.test',
            'company_id': cls.env.company.id,
        })
        program = cls.env['loyalty.program'].create({
            'name': 'Rendering Consign', 'program_type': 'consign', 'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        product = cls.env['product.product'].create({'name': 'Rendered Product', 'type': 'service'})
        operation = cls.env['loyalty.consign.engine']._issue(
            source=partner, partner=partner, program=program,
            grants=[{'product': product, 'quantity': 3}], idempotency_key='test:render:issue',
        )
        cls.card = cls.env['loyalty.card'].browse(operation.result_json['card_id'])

    def test_recipient_balance_lines_are_authoritative_available_projections(self):
        line = self.card._consign_recipient_balance_lines()
        self.assertEqual(line, self.card.consign_line_ids)
        self.assertEqual(line.qty_available, 3)

    def test_mail_template_renders_current_available_balance_not_legacy_history(self):
        template = self.env.ref('woow_loyalty_consign.mail_template_consign_card')
        rendered = template._render_field('body_html', [self.card.id])[self.card.id]
        self.assertIn('Rendered Product', rendered)
        self.assertIn('3.0', rendered)
        self.assertNotIn('qty_deposited', rendered)
        self.assertNotIn('date_expiry', rendered)
