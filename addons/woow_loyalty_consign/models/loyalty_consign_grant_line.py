from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class LoyaltyConsignGrantLine(models.Model):
    _name = 'loyalty.consign.grant.line'
    _description = 'Consignment Grant Entitlement'
    _order = 'rule_id, id'
    _rec_name = 'entitlement_product_id'
    _check_company_auto = True

    rule_id = fields.Many2one(
        'loyalty.consign.grant.rule',
        string='授權規則',
        required=True,
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related='rule_id.company_id',
        store=True,
        index=True,
    )
    entitlement_product_id = fields.Many2one(
        'product.product',
        string='授權產品',
        required=True,
        index=True,
        check_company=True,
    )
    entitlement_uom_category_id = fields.Many2one(
        related='entitlement_product_id.uom_id.category_id',
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='計量單位',
        required=True,
        domain="[('category_id', '=', entitlement_uom_category_id)]",
    )
    quantity = fields.Float(
        string='授權數量',
        required=True,
        digits='Product Unit of Measure',
    )

    @api.model_create_multi
    def create(self, vals_list):
        # ``quantity`` is database-required, so reject omitted/false values
        # before INSERT can expose a PostgreSQL NotNullViolation to callers.
        if any(not vals.get('quantity') for vals in vals_list):
            raise ValidationError(_('授權數量必須大於零。'))

        lines = super().create(vals_list)
        # Retain unconditional rounded-quantity and UoM-category validation
        # for every post-create record, regardless of provided field set.
        lines._check_positive_quantity()
        lines._check_uom_category()
        return lines

    @api.onchange('entitlement_product_id')
    def _onchange_entitlement_product_id(self):
        for line in self:
            if line.entitlement_product_id:
                line.product_uom_id = line.entitlement_product_id.uom_id
            else:
                line.product_uom_id = False

    @api.constrains('quantity', 'product_uom_id')
    def _check_positive_quantity(self):
        for line in self:
            if not line.product_uom_id:
                continue
            if float_compare(
                line.quantity,
                0.0,
                precision_rounding=line.product_uom_id.rounding,
            ) <= 0:
                raise ValidationError(_(
                    '授權數量必須依「%(uom)s」的精度大於零。',
                    uom=line.product_uom_id.display_name,
                ))

    @api.constrains('entitlement_product_id', 'product_uom_id')
    def _check_uom_category(self):
        for line in self:
            if not line.entitlement_product_id or not line.product_uom_id:
                continue
            if (
                line.product_uom_id.category_id
                != line.entitlement_product_id.uom_id.category_id
            ):
                raise ValidationError(_(
                    '授權產品「%(product)s」與計量單位「%(uom)s」的類別必須相同。',
                    product=line.entitlement_product_id.display_name,
                    uom=line.product_uom_id.display_name,
                ))

    def write(self, vals):
        previous_rules = self.rule_id
        result = super().write(vals)
        (previous_rules | self.rule_id).exists()._check_has_grant_lines()
        return result

    def unlink(self):
        rules = self.rule_id
        result = super().unlink()
        rules.exists()._check_has_grant_lines()
        return result
