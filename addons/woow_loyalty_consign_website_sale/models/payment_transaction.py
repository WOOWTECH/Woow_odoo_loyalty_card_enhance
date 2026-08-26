from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    consign_order_id = fields.Many2one('sale.order', readonly=True, copy=False, index=True)
    consign_allocation_version = fields.Integer(readonly=True, copy=False)
    consign_hold_id = fields.Many2one('loyalty.consign.hold', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        transactions = super().create(vals_list)
        transactions._bind_website_consign_hold()
        return transactions

    def write(self, vals):
        result = super().write(vals)
        if 'sale_order_ids' in vals:
            self._bind_website_consign_hold()
        return result

    def _bind_website_consign_hold(self):
        """Snapshot one current Hold while a payment transaction is assembled."""
        for tx in self.filtered(lambda t: not t.consign_order_id and t.sale_order_ids):
            orders = tx.sale_order_ids.filtered('consign_hold_operation_id')
            if len(orders) != 1:
                continue
            order = orders
            order._lock_website_consign_payment()
            order.invalidate_recordset(['consign_hold_operation_id', 'consign_hold_version'])
            holds = order.consign_hold_operation_id.hold_ids.filtered(lambda h: h.state == 'active')
            if len(holds) != 1 or order.consign_hold_version != order.consign_allocation_version:
                continue
            tx.sudo().write({
                'consign_order_id': order.id,
                'consign_allocation_version': order.consign_allocation_version,
                'consign_hold_id': holds.id,
            })

    def _post_process(self):
        result = super()._post_process()
        for tx in self:
            if tx.state == 'done':
                tx._capture_website_consign_hold()
            elif tx.state in ('error', 'cancel'):
                tx._release_website_consign_hold()
        return result

    def _capture_website_consign_hold(self):
        self.ensure_one()
        if not self.consign_order_id:
            return False
        order = self.consign_order_id
        order._lock_website_consign_payment()
        order.invalidate_recordset(['consign_allocation_version', 'consign_payment_transaction_id'])
        if order.consign_allocation_version != self.consign_allocation_version:
            raise ValidationError('The checkout cart changed before payment completion.')
        if order.consign_payment_transaction_id and order.consign_payment_transaction_id != self:
            raise ValidationError('Another payment transaction already owns this checkout version.')
        hold = self.consign_hold_id.exists()
        if not hold or hold.state != 'active':
            # A repeat callback after a successful capture has no side effect.
            if order.consign_payment_transaction_id == self and hold and hold.state == 'captured':
                return order.consign_capture_operation_id
            raise ValidationError('The checkout authorization is no longer available.')
        operation = self.env['loyalty.consign.engine']._capture(
            order, order.partner_id, hold,
            'website-payment-capture-%s-%s-%s' % (self.id, order.id, self.consign_allocation_version),
        )
        order.sudo().write({
            'consign_payment_transaction_id': self.id,
            'consign_capture_operation_id': operation.id,
        })
        return operation

    def _release_website_consign_hold(self):
        self.ensure_one()
        if not self.consign_order_id:
            return False
        order = self.consign_order_id
        order._lock_website_consign_payment()
        order.invalidate_recordset(['consign_allocation_version', 'consign_payment_transaction_id'])
        # Never let a stale callback release a Hold belonging to a newer cart.
        if order.consign_allocation_version != self.consign_allocation_version or order.consign_payment_transaction_id:
            return False
        hold = self.consign_hold_id.exists()
        if not hold or hold.state != 'active':
            return False
        return self.env['loyalty.consign.engine']._release(
            order, order.partner_id, hold,
            'website-payment-release-%s-%s-%s' % (self.id, order.id, self.consign_allocation_version),
        )
