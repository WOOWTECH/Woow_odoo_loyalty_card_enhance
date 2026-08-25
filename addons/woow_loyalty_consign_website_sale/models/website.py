from odoo import fields, models

class Website(models.Model):
    _inherit = 'website'
    consign_redemption_enabled = fields.Boolean(default=False, company_dependent=True)
