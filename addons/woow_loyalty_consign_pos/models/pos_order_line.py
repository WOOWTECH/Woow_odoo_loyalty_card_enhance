from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='Consignment Line',
    )
    is_consign_redemption = fields.Boolean(
        string='Is Consignment Redemption', default=False,
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        params = super()._load_pos_data_fields(config_id)
        params += ['consign_line_id', 'is_consign_redemption']
        return params
