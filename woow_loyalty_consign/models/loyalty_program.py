from odoo import api, fields, models


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    program_type = fields.Selection(
        selection_add=[('consign', '寄品卡')],
        ondelete={'consign': 'set default'},
    )
    consign_card_type = fields.Selection(
        [
            ('wine', '酒窖寄存'),
            ('aesthetics', '醫美療程'),
            ('general', '一般寄品'),
        ],
        string='寄品卡類型',
        default='general',
    )
    consign_expiry_months = fields.Integer(
        string='品項有效月數',
        default=0,
        help='0 表示無期限。設定後，每筆品項的到期日 = 存入日 + 此月數。',
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
            self.consign_card_type = self.consign_card_type or 'general'
            if not self.mail_template_id:
                template = self.env.ref(
                    'woow_loyalty_consign.mail_template_consign_card',
                    raise_if_not_found=False,
                )
                self.mail_template_id = template
