from odoo import api, fields, models


class LoyaltyHistory(models.Model):
    _inherit = 'loyalty.history'

    balance_before = fields.Float(string="前餘額", default=0)
    balance_after = fields.Float(string="後餘額", default=0)

    @api.model_create_multi
    def create(self, vals_list):
        """Record balance_before / balance_after when creating history entries.

        Odoo writes loyalty.history AFTER updating card.points, so
        card.points at this moment is already the post-transaction balance.
        """
        for vals in vals_list:
            if 'balance_before' not in vals or 'balance_after' not in vals:
                card = self.env['loyalty.card'].browse(vals.get('card_id'))
                issued = vals.get('issued', 0)
                used = vals.get('used', 0)
                balance_after = card.points
                balance_before = balance_after - issued + used
                vals['balance_before'] = balance_before
                vals['balance_after'] = balance_after
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Defensive helpers for portal templates (fallback if not in core)
    # ------------------------------------------------------------------

    def _get_order_portal_url(self):
        """Return a portal URL for the linked order, or empty string."""
        self.ensure_one()
        if hasattr(super(), '_get_order_portal_url'):
            return super()._get_order_portal_url()
        order = self.order_id if 'order_id' in self._fields else False
        if order and hasattr(order, 'get_portal_url'):
            return order.get_portal_url()
        return ''

    def _get_order_description(self):
        """Return a human-readable description for the linked order."""
        self.ensure_one()
        if hasattr(super(), '_get_order_description'):
            return super()._get_order_description()
        order = self.order_id if 'order_id' in self._fields else False
        if order:
            return order.name or ''
        return ''
