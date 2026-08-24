from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    program_type = fields.Selection(
        selection_add=[('consign', '寄品卡')],
        ondelete={'consign': 'set default'},
    )
    consign_card_count = fields.Integer(
        string='寄品卡數量', compute='_compute_consign_card_count',
    )
    consign_grant_rule_ids = fields.One2many(
        'loyalty.consign.grant.rule',
        'program_id',
        string='寄品授權規則',
        copy=True,
    )
    consign_trigger_product_ids = fields.Many2many(
        'product.product',
        string='寄品觸發產品',
        compute='_compute_consign_trigger_product_ids',
    )

    @api.depends('consign_grant_rule_ids.trigger_product_id')
    def _compute_consign_trigger_product_ids(self):
        for program in self:
            program.consign_trigger_product_ids = (
                program.consign_grant_rule_ids.mapped('trigger_product_id')
            )

    @api.constrains('program_type', 'active', 'company_id', 'consign_grant_rule_ids')
    def _check_consign_grant_rules(self):
        for program in self:
            if program.consign_grant_rule_ids and program.program_type != 'consign':
                raise ValidationError(_('只有寄品方案可以設定寄品授權規則。'))
            if program.active:
                program.consign_grant_rule_ids._check_active_trigger_overlap()

    def _compute_consign_card_count(self):
        for program in self:
            if program.program_type == 'consign':
                program.consign_card_count = self.env['loyalty.card'].sudo().search_count([
                    ('program_id', '=', program.id),
                ])
            else:
                program.consign_card_count = 0

    def action_view_consign_cards(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': '寄品卡',
            'res_model': 'loyalty.card',
            'view_mode': 'list,form',
            'domain': [('program_id', '=', self.id)],
        }

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
