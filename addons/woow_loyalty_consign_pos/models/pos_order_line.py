from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    # POS clients submit a selected card and requested coverage only.  The
    # backend always re-derives product/UoM/quantity from persisted POS lines.
    consign_card_id = fields.Many2one(
        'loyalty.card', string='Consignment Card', ondelete='restrict',
    )
    consign_covered_qty = fields.Float(
        string='Consignment Covered Quantity', default=0.0,
    )
    # Retained as a read-only historical compatibility field; Task 15 never
    # accepts it as engine input.
    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='Consignment Line', readonly=True,
    )
    is_consign_redemption = fields.Boolean(
        string='Is Consignment Redemption', default=False, readonly=True,
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        params = super()._load_pos_data_fields(config_id)
        params += [
            'consign_card_id', 'consign_covered_qty', 'consign_line_id',
            'is_consign_redemption',
        ]
        return params
