from odoo import _, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    enable_consign_redemption = fields.Boolean(
        string='Enable Consignment Redemption', default=False,
        help='Allow online, server-authorized consignment redemption in this POS.',
    )

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
        if not self.enable_consign_redemption:
            return {
                'successful': False,
                'payload': {'error_message': _('Consignment redemption is disabled for this POS.')},
            }
        card = self.env['loyalty.card'].search([
            ('code', '=', code),
            ('is_consign', '=', True),
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('program_id.active', '=', True),
        ], limit=1)

        if not card:
            return {
                'successful': False,
                'payload': {'error_message': _('Consignment card not found.'), 'not_found': True},
            }

        # An exact barcode may auto-select its owner.  If the cashier already
        # selected a customer, ownership must still match exactly.
        if partner_id and card.partner_id and card.partner_id.id != partner_id:
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
        if not self.enable_consign_redemption:
            return {
                'successful': False,
                'payload': {'error_message': _('Consignment redemption is disabled for this POS.')},
            }
        if not partner_id:
            return {'successful': False, 'payload': {'error_message': _('No customer selected.')}}

        cards = self.env['loyalty.card'].search([
            ('is_consign', '=', True),
            ('active', '=', True),
            ('partner_id', '=', partner_id),
            ('company_id', '=', self.company_id.id),
            ('program_id.active', '=', True),
        ])

        result = []
        for card in cards:
            active_lines = card.consign_line_ids.filtered(
                lambda line: line.state == 'active' and line.qty_available > 0
            )
            if not active_lines:
                continue
            payload = self._prepare_consign_card_payload(card, active_lines)
            payload['active_items'] = len(active_lines)
            payload['total_available_qty'] = sum(
                line.qty_available for line in active_lines
            )
            result.append(payload)

        return {'successful': True, 'payload': {'cards': result}}

    def _prepare_consign_card_payload(self, card, active_lines=None):
        """Build the standard card payload dict sent to the POS frontend."""
        if active_lines is None:
            active_lines = card.consign_line_ids.filtered(
                lambda line: line.state == 'active' and line.qty_available > 0
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
                'qty_available': line.qty_available,
                'uom_rounding': line.product_uom_id.rounding,
                'unit_price': line.unit_price,
            } for line in active_lines],
        }
