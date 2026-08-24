from odoo import _, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    def _get_consign_redemption_product_id(self):
        """Return the consign redemption service product ID."""
        product = self.env.ref(
            'woow_loyalty_consign.consign_redemption_product',
            raise_if_not_found=False,
        )
        return product.id if product else False

    def use_consign_card_code(self, code, partner_id):
        """Validate a consignment card by barcode code and return card data."""
        self.ensure_one()
        card = self.env['loyalty.card'].search([
            ('code', '=', code),
            ('is_consign', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not card:
            return {
                'successful': False,
                'payload': {'error_message': _('Consignment card not found.'), 'not_found': True},
            }

        # 驗證卡片擁有者：要求必須指定客戶，且與卡片擁有者一致
        if not partner_id:
            return {
                'successful': False,
                'payload': {'error_message': _('Please select a customer before scanning a consignment card.')},
            }
        if card.partner_id and card.partner_id.id != partner_id:
            return {
                'successful': False,
                'payload': {'error_message': _('This card does not belong to the selected customer.')},
            }

        return {
            'successful': True,
            'payload': self._prepare_consign_card_payload(card),
        }

    def get_partner_consign_cards(self, partner_id):
        """Get all active consignment cards for a specific partner."""
        self.ensure_one()
        if not partner_id:
            return {'successful': False, 'payload': {'error_message': _('No customer selected.')}}

        cards = self.env['loyalty.card'].search([
            ('is_consign', '=', True),
            ('active', '=', True),
            ('partner_id', '=', partner_id),
        ])

        result = []
        for card in cards:
            active_lines = card.consign_line_ids.filtered(
                lambda l: l.state == 'active' and l.qty_remaining > 0
            )
            if not active_lines:
                continue
            payload = self._prepare_consign_card_payload(card, active_lines)
            payload['active_items'] = len(active_lines)
            payload['total_remaining_qty'] = sum(l.qty_remaining for l in active_lines)
            result.append(payload)

        return {'successful': True, 'payload': {'cards': result}}

    def _prepare_consign_card_payload(self, card, active_lines=None):
        """Build the standard card payload dict sent to the POS frontend."""
        if active_lines is None:
            active_lines = card.consign_line_ids.filtered(
                lambda l: l.state == 'active' and l.qty_remaining > 0
            )
        return {
            'card_id': card.id,
            'card_code': card.code,
            'partner_id': card.partner_id.id,
            'partner_name': card.partner_id.name,
            'program_name': card.program_id.name,
            'consign_redemption_product_id': self._get_consign_redemption_product_id(),
            'lines': [{
                'id': line.id,
                'product_id': line.product_id.id,
                'product_name': line.product_desc or line.product_id.display_name,
                'qty_remaining': line.qty_remaining,
                'unit_price': line.unit_price,
            } for line in active_lines],
        }
