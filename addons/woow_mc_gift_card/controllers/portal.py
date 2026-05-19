from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class GiftCardPortal(MemberCenterPortal):

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id
        cards = request.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'gift_card'),
            ('active', '=', True),
        ])
        if cards:
            values.update({
                'show_gift_card': True,
                'gift_balance': sum(cards.mapped('points')),
                'gift_point_name': cards[:1].point_name or '元',
                'currency': request.env.company.currency_id,
            })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            count = request.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('program_id.program_type', '=', 'gift_card'),
                ('active', '=', True),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + count
        return values

    @http.route(
        '/my/member-center/gift-cards',
        type='http', auth='user', website=True,
    )
    def portal_mc_gift_card_list(self, **kw):
        cards = self._get_cards_by_type('gift_card')
        currency = request.env.company.currency_id
        values = {
            'page_name': 'mc_gift_cards',
            'cards': cards,
            'currency': currency,
        }
        return request.render('woow_mc_gift_card.portal_mc_gift_card_list', values)

    @http.route(
        '/my/member-center/gift-cards/<int:card_id>',
        type='http', auth='user', website=True,
    )
    def portal_mc_gift_card_detail(self, card_id, **kw):
        card = self._get_single_card(card_id, 'gift_card')
        if not card:
            return request.redirect('/my/member-center/gift-cards')
        currency = request.env.company.currency_id
        values = {
            'page_name': 'mc_gift_card_detail',
            'card': card,
            'currency': currency,
        }
        values.update(self._get_card_history_values(card, 'gift-cards'))
        values = self._get_page_view_values(
            card, card._portal_ensure_token(), values, 'my_mc_gift_card_history', False, **kw,
        )
        return request.render('woow_mc_gift_card.portal_mc_gift_card_detail', values)
