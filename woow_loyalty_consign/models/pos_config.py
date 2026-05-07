from odoo import models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    def _get_consign_redemption_product_id(self):
        """Return the consign redemption service product ID (XML ID lookup)."""
        product = self.env.ref(
            'woow_loyalty_consign.consign_redemption_product',
            raise_if_not_found=False,
        )
        return product.id if product else False

    def use_consign_card_code(self, code, partner_id):
        """POS 掃碼驗證寄品卡，回傳卡片資訊及可核銷品項。"""
        self.ensure_one()
        card = self.env['loyalty.card'].search([
            ('code', '=', code),
            ('is_consign', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not card:
            return {
                'successful': False,
                'payload': {'error_message': '找不到此寄品卡。', 'not_found': True},
            }

        if card.partner_id and partner_id and card.partner_id.id != partner_id:
            return {
                'successful': False,
                'payload': {'error_message': '此寄品卡不屬於此客戶。'},
            }

        lines = []
        for line in card.consign_line_ids.filtered(
            lambda l: l.state == 'active' and l.qty_remaining > 0
        ):
            lines.append({
                'id': line.id,
                'product_id': line.product_id.id,
                'product_name': line.product_desc or line.product_id.display_name,
                'qty_remaining': line.qty_remaining,
                'unit_price': line.unit_price,
                'date_expiry': line.date_expiry.isoformat() if line.date_expiry else False,
            })

        return {
            'successful': True,
            'payload': {
                'card_id': card.id,
                'card_code': card.code,
                'partner_id': card.partner_id.id,
                'partner_name': card.partner_id.name,
                'program_name': card.program_id.name,
                'consign_redemption_product_id': self._get_consign_redemption_product_id(),
                'lines': lines,
            },
        }
