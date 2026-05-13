from odoo import api, fields, models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    program_type = fields.Selection(
        selection_add=[('consign', '寄品卡')],
        ondelete={'consign': 'set default'},
    )

    @api.depends('program_type')
    def _compute_is_nominative(self):
        super()._compute_is_nominative()
        for program in self:
            if program.program_type == 'consign':
                program.is_nominative = True

    @api.depends('program_type')
    def _compute_is_payment_program(self):
        super()._compute_is_payment_program()
        for program in self:
            if program.program_type == 'consign':
                program.is_payment_program = False

    def _program_type_default_values(self):
        res = super()._program_type_default_values()
        res['consign'] = {
            'applies_on': 'future',
            'trigger': 'auto',
            'portal_visible': True,
            'portal_point_name': '品項',
            'is_nominative': True,
            'rule_ids': [(5, 0, 0)],
            'reward_ids': [(5, 0, 0)],
            'communication_plan_ids': [(5, 0, 0)],
        }
        return res

    def _program_items_name(self):
        res = super()._program_items_name()
        res['consign'] = '寄品卡'
        return res

    @api.onchange('program_type')
    def _onchange_program_type_consign(self):
        if self.program_type == 'consign':
            if not self.mail_template_id:
                template = self.env.ref(
                    'woow_loyalty_consign.mail_template_consign_card',
                    raise_if_not_found=False,
                )
                self.mail_template_id = template
