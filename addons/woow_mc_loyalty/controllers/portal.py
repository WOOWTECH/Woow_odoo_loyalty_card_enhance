from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class LoyaltyPortal(MemberCenterPortal):

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id
        cards = request.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'loyalty'),
            ('active', '=', True),
        ])
        if cards:
            values.update({
                'show_loyalty': True,
                'loyalty_points': sum(cards.mapped('points')),
                'loyalty_point_name': cards[:1].point_name or '點',
            })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            count = request.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('program_id.program_type', '=', 'loyalty'),
                ('active', '=', True),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + count
        return values

    @http.route(
        '/my/member-center/loyalty',
        type='http', auth='user', website=True,
    )
    def portal_mc_loyalty_list(self, **kw):
        cards = self._get_cards_by_type('loyalty')
        values = {
            'page_name': 'mc_loyalty',
            'cards': cards,
        }
        return request.render('woow_mc_loyalty.portal_mc_loyalty_list', values)

    @http.route(
        '/my/member-center/loyalty/<int:card_id>',
        type='http', auth='user', website=True,
    )
    def portal_mc_loyalty_detail(self, card_id, **kw):
        card = self._get_single_card(card_id, 'loyalty')
        if not card:
            return request.redirect('/my/member-center/loyalty')
        values = {
            'page_name': 'mc_loyalty_detail',
            'card': card,
        }
        values.update(self._get_card_history_values(card, 'loyalty'))
        values = self._get_page_view_values(
            card, card._portal_ensure_token(), values, 'my_mc_loyalty_history', False, **kw,
        )
        return request.render('woow_mc_loyalty.portal_mc_loyalty_detail', values)
