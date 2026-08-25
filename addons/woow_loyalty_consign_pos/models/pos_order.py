import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    _inherit = 'pos.order'

    consign_hold_id = fields.Many2one(
        'loyalty.consign.hold', readonly=True, copy=False, ondelete='restrict',
    )
    consign_authorize_operation_id = fields.Many2one(
        'loyalty.consign.operation', readonly=True, copy=False, ondelete='restrict',
    )
    consign_capture_operation_id = fields.Many2one(
        'loyalty.consign.operation', readonly=True, copy=False, ondelete='restrict',
    )
    consign_allocation_hash = fields.Char(readonly=True, copy=False, index=True)
    consign_state = fields.Selection(
        [('none', 'None'), ('authorized', 'Authorized'), ('captured', 'Captured'),
         ('released', 'Released'), ('exception', 'Payment Exception')],
        default='none', readonly=True, copy=False,
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_to_load = super()._load_pos_data_fields(config_id)
        return fields_to_load + [
            'consign_hold_id', 'consign_authorize_operation_id',
            'consign_capture_operation_id', 'consign_allocation_hash',
            'consign_state',
        ]

    def _consign_requests_from_persisted_lines(self):
        """Derive engine input exclusively from persisted POS order lines."""
        self.ensure_one()
        requests = []
        for line in self.lines.filtered('consign_card_id'):
            quantity = line.consign_covered_qty
            if quantity <= 0:
                continue
            requests.append({
                'card_id': line.consign_card_id.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id,
                'quantity': quantity,
            })
        return requests

    @staticmethod
    def _consign_request_hash(requests):
        canonical = sorted(
            requests,
            key=lambda item: (
                item['card_id'], item['product_id'], item['product_uom_id'],
                item['quantity'],
            ),
        )
        encoded = json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _authorize_consign_redemption(self):
        """Private POS backend seam; never trust frontend line identifiers."""
        self.ensure_one()
        if not self.config_id.enable_consign_redemption:
            raise ValidationError(_('Consignment redemption is disabled for this POS.'))
        if not self.partner_id:
            raise ValidationError(_('A customer is required for consignment redemption.'))
        requests = self._consign_requests_from_persisted_lines()
        if not requests:
            return False
        allocation_hash = self._consign_request_hash(requests)
        if (
            self.consign_state == 'authorized'
            and self.consign_hold_id.state == 'active'
            and self.consign_allocation_hash == allocation_hash
        ):
            return self.consign_authorize_operation_id
        if self.consign_hold_id and self.consign_hold_id.state == 'active':
            self.env['loyalty.consign.engine']._release(
                self, self.partner_id, self.consign_hold_id,
                'pos-release-replaced-%s-%s' % (self.id, self.consign_hold_id.id),
            )
        operation = self.env['loyalty.consign.engine']._authorize(
            self, self.partner_id, requests,
            'pos-authorize-%s-%s' % (self.id, allocation_hash),
        )
        hold = operation.hold_ids
        if len(hold) != 1:
            raise ValidationError(_('The POS consignment authorization is invalid.'))
        self.sudo().write({
            'consign_hold_id': hold.id,
            'consign_authorize_operation_id': operation.id,
            'consign_allocation_hash': allocation_hash,
            'consign_state': 'authorized',
        })
        return operation

    def _capture_consign_redemption(self):
        """Capture a current POS Hold; errors abort POS sync rather than being hidden."""
        self.ensure_one()
        if self.consign_state == 'captured':
            return self.consign_capture_operation_id
        if self.consign_state != 'authorized' or not self.consign_hold_id:
            return False
        hold = self.consign_hold_id
        if hold.state != 'active' or hold.expires_at <= fields.Datetime.now():
            self.sudo().write({'consign_state': 'exception'})
            raise ValidationError(_(
                'The POS consignment Hold expired before payment finalization. '
                'Do not complete this payment; re-authorize it online.'
            ))
        operation = self.env['loyalty.consign.engine']._capture(
            self, self.partner_id, hold,
            'pos-capture-%s-%s' % (self.id, self.consign_allocation_hash),
        )
        self.sudo().write({
            'consign_capture_operation_id': operation.id,
            'consign_state': 'captured',
        })
        return operation

    def _release_consign_redemption(self):
        self.ensure_one()
        if self.consign_state != 'authorized' or not self.consign_hold_id:
            return False
        hold = self.consign_hold_id
        if hold.state != 'active':
            return False
        operation = self.env['loyalty.consign.engine']._release(
            self, self.partner_id, hold,
            'pos-release-%s-%s' % (self.id, self.consign_allocation_hash),
        )
        self.sudo().write({'consign_state': 'released'})
        return operation

    @api.model
    def _process_order(self, order, existing_order):
        """Persist lines first, then derive/capture trusted consign coverage atomically."""
        result = super()._process_order(order, existing_order)
        pos_order = self.browse(result) if isinstance(result, int) else result
        if pos_order and pos_order.config_id.enable_consign_redemption:
            requests = pos_order._consign_requests_from_persisted_lines()
            if requests:
                pos_order._authorize_consign_redemption()
                if pos_order.state in ('paid', 'done', 'invoiced'):
                    pos_order._capture_consign_redemption()
        return result

    def action_pos_order_cancel(self):
        self._release_consign_redemption()
        return super().action_pos_order_cancel()
