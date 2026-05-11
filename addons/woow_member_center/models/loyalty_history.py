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
