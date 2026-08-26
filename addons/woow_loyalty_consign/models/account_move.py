from odoo import models
from odoo.tools import float_compare, float_round


class AccountMove(models.Model):
    """Trusted paid-invoice adapter for consign entitlement grants.

    The payment state is authoritative.  Sales confirmation intentionally has
    no consignment side effect: an invoice line can only create its grant once
    the customer invoice has reached ``paid``.  Each grant command is keyed by
    the durable paid-invoice/sale-line/rule tuple, so reconciliation callbacks
    replay the exact engine operation rather than appending a duplicate movement.
    """

    _inherit = 'account.move'

    def _invoice_paid_hook(self):
        result = super()._invoice_paid_hook()
        for invoice in self.filtered(lambda move: (
            move.move_type == 'out_invoice'
            and move.state == 'posted'
            and move.payment_state == 'paid'
        )):
            invoice._issue_consign_paid_invoice_grants()
        return result

    def _lock_consign_sale_lines(self, sale_lines):
        """Fence concurrent payment callbacks before calculating a grant."""
        for sale_line in sale_lines.sorted('id'):
            self.env.cr.execute(
                'UPDATE sale_order_line SET write_date = write_date '
                'WHERE id = %s RETURNING id', (sale_line.id,),
            )
            if self.env.cr.rowcount != 1:
                return False
        return True

    def _is_consign_product_invoice_line(self, line):
        """Return whether an invoice line represents a sold/refunded product.

        Odoo 18 stores ordinary invoice rows with ``display_type='product'``;
        only section/note/accounting rows must be excluded.  Accepting the
        historical false value keeps upgraded rows eligible as well.
        """
        return line.display_type in (False, 'product')

    def _paid_trigger_quantity(self, sale_line, trigger_uom):
        """Net paid quantity for a sale line, in the rule trigger UoM.

        Refund invoices deliberately reduce the paid business quantity but do
        not append a reversal here (that controlled lifecycle belongs to the
        refund saga).  This cap means a later credit/reinvoice cannot issue a
        second copy of an entitlement that was already issued for the sale.
        """
        quantity = 0.0
        for invoice_line in sale_line.invoice_lines.filtered(
            lambda line: line.move_id.state == 'posted'
            and line.move_id.payment_state == 'paid'
            and line.move_id.move_type in ('out_invoice', 'out_refund')
            and self._is_consign_product_invoice_line(line)
        ):
            line_quantity = invoice_line.product_uom_id._compute_quantity(
                invoice_line.quantity, trigger_uom, round=False,
            )
            quantity += line_quantity * (
                -1 if invoice_line.move_id.move_type == 'out_refund' else 1
            )
        return float_round(quantity, precision_rounding=trigger_uom.rounding)

    def _issued_trigger_quantity(self, sale_line, program, grant_line, trigger_uom):
        """Convert prior issue facts for one grant back into trigger units."""
        movements = self.env['loyalty.consign.movement'].search([
            ('movement_type', '=', 'issue'),
            ('source_model', '=', 'sale.order.line'),
            ('source_res_id', '=', sale_line.id),
            ('card_id.program_id', '=', program.id),
            (
                'operation_id.idempotency_key', 'like',
                'consign:paid-invoice-grant:v1:%%:%s:%s:%s' % (
                    sale_line.id, program.id, grant_line.id,
                ),
            ),
        ])
        product = grant_line.entitlement_product_id
        issued_grant_uom = sum(product.uom_id._compute_quantity(
            movement.quantity, grant_line.product_uom_id, round=False,
        ) for movement in movements if movement.product_id == product)
        if not grant_line.quantity:
            return 0.0
        return float_round(
            issued_grant_uom / grant_line.quantity,
            precision_rounding=trigger_uom.rounding,
        )

    def _issue_consign_paid_invoice_grants(self):
        self.ensure_one()
        if self.move_type != 'out_invoice' or self.state != 'posted' or self.payment_state != 'paid':
            return
        engine = self.env['loyalty.consign.engine']
        programs = self.env['loyalty.program'].search([
            ('program_type', '=', 'consign'),
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('consign_grant_rule_ids', '!=', False),
        ], order='id')
        sale_lines = self.invoice_line_ids.filtered(
            lambda line: self._is_consign_product_invoice_line(line)
            and line.quantity > 0
        ).mapped('sale_line_ids').filtered(
            lambda line: line.company_id == self.company_id
            and line.order_id.partner_id == self.partner_id
        )
        if not programs or not sale_lines or not self._lock_consign_sale_lines(sale_lines):
            return
        for sale_line in sale_lines.sorted(lambda line: (line.sequence, line.id)):
            for program in programs:
                for rule in program.consign_grant_rule_ids.filtered(
                    lambda candidate: candidate.trigger_product_id == sale_line.product_id
                ).sorted('id'):
                    paid_trigger_quantity = self._paid_trigger_quantity(
                        sale_line, rule.trigger_product_id.uom_id,
                    )
                    if float_compare(
                        paid_trigger_quantity, 0.0,
                        precision_rounding=rule.trigger_product_id.uom_id.rounding,
                    ) <= 0:
                        continue
                    for grant_line in rule.grant_line_ids.sorted('id'):
                        issued_trigger_quantity = self._issued_trigger_quantity(
                            sale_line, program, grant_line,
                            rule.trigger_product_id.uom_id,
                        )
                        trigger_quantity = paid_trigger_quantity - issued_trigger_quantity
                        if float_compare(
                            trigger_quantity, 0.0,
                            precision_rounding=rule.trigger_product_id.uom_id.rounding,
                        ) <= 0:
                            continue
                        product = grant_line.entitlement_product_id
                        quantity = grant_line.product_uom_id._compute_quantity(
                            grant_line.quantity * trigger_quantity,
                            product.uom_id, round=False,
                        )
                        quantity = float_round(
                            quantity, precision_rounding=product.uom_id.rounding,
                        )
                        if float_compare(
                            quantity, 0.0, precision_rounding=product.uom_id.rounding,
                        ) <= 0:
                            continue
                        engine._issue(
                            source=self,
                            partner=self.partner_id,
                            program=program,
                            grants=[{
                                'product': product,
                                'product_uom': product.uom_id,
                                'quantity': quantity,
                                'source_line': sale_line,
                                'source_channel': 'sale',
                                'provenance_key': (
                                    f'paid-invoice:{self.id}:sale-line:{sale_line.id}:'
                                    f'grant-line:{grant_line.id}'
                                ),
                                'product_desc': product.display_name,
                            }],
                            idempotency_key=(
                                f'consign:paid-invoice-grant:v1:{self.id}:'
                                f'{sale_line.id}:{program.id}:{grant_line.id}'
                            ),
                        )
