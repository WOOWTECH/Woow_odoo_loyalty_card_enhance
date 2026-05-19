from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class EwalletPortal(MemberCenterPortal):

    # ----------------------------------------------------------------
    # Hub Values
    # ----------------------------------------------------------------

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id
        cards = request.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'ewallet'),
            ('active', '=', True),
        ])
        if cards:
            values.update({
                'show_ewallet': True,
                'ewallet_balance': sum(cards.mapped('points')),
                'ewallet_point_name': cards[:1].point_name or '元',
                'currency': request.env.company.currency_id,
            })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            count = request.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('program_id.program_type', '=', 'ewallet'),
                ('active', '=', True),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + count
        return values

    # ----------------------------------------------------------------
    # eWallet List
    # ----------------------------------------------------------------

    @http.route(
        '/my/member-center/ewallet',
        type='http', auth='user', website=True,
    )
    def portal_mc_ewallet(self, **kw):
        cards = self._get_cards_by_type('ewallet')
        total_balance = sum(cards.mapped('points'))
        point_name = cards[:1].point_name if cards else '元'
        currency = request.env.company.currency_id
        values = {
            'page_name': 'mc_ewallet',
            'cards': cards,
            'total_balance': total_balance,
            'point_name': point_name,
            'currency': currency,
        }
        return request.render('woow_mc_ewallet.portal_mc_ewallet', values)

    # ----------------------------------------------------------------
    # eWallet Detail
    # ----------------------------------------------------------------

    @http.route(
        '/my/member-center/ewallet/<int:card_id>',
        type='http', auth='user', website=True,
    )
    def portal_mc_ewallet_detail(self, card_id, **kw):
        card = self._get_single_card(card_id, 'ewallet')
        if not card:
            return request.redirect('/my/member-center/ewallet')
        values = {
            'page_name': 'mc_ewallet_detail',
            'card': card,
        }
        values.update(self._get_card_history_values(card, 'ewallet'))
        values = self._get_page_view_values(
            card, card._portal_ensure_token(), values, 'my_mc_ewallet_history', False, **kw,
        )
        return request.render('woow_mc_ewallet.portal_mc_ewallet_detail', values)
