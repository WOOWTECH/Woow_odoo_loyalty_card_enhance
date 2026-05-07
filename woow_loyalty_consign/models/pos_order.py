from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def confirm_consign_redemptions(self, consign_data):
        """POS 結帳後建立寄品核銷紀錄。

        Args:
            consign_data: {
                'card_id': int,
                'lines': [{'consign_line_id': int, 'qty_redeemed': float, 'note': str}],
            }
        """
        self.ensure_one()
        if not consign_data or not consign_data.get('lines'):
            return {'successful': True, 'payload': {}}

        card = self.env['loyalty.card'].browse(consign_data['card_id'])
        if not card.exists() or not card.is_consign:
            return {
                'successful': False,
                'payload': {'error_message': '寄品卡不存在或非寄品卡類型。'},
            }

        redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': card.id,
            'staff_user_id': self.env.user.id,
            'service_note': f'POS 核銷 - {self.name}',
            'line_ids': [(0, 0, {
                'consign_line_id': line['consign_line_id'],
                'qty_redeemed': line['qty_redeemed'],
                'note': line.get('note', ''),
            }) for line in consign_data['lines']],
        })
        redemption.action_done()

        return {
            'successful': True,
            'payload': {
                'redemption_id': redemption.id,
                'redemption_name': redemption.name,
            },
        }
