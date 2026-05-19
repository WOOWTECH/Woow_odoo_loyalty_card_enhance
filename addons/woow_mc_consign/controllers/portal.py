from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class ConsignMcPortal(MemberCenterPortal):

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id
        consign_count = request.env['loyalty.card'].sudo().search_count([
            ('partner_id', '=', partner.id),
            ('is_consign', '=', True),
            ('active', '=', True),
        ])
        if consign_count:
            values.update({
                'show_consign': True,
                'consign_count': consign_count,
            })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            count = request.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('is_consign', '=', True),
                ('active', '=', True),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + count
        return values

    @http.route(
        ['/my/consign-cards', '/my/consign-cards/page/<int:page>'],
        type='http', auth='user', website=True,
    )
    def portal_my_consign_cards(self, page=1, **kw):
        partner = request.env.user.partner_id
        cards = request.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner.id),
            ('is_consign', '=', True),
            ('active', '=', True),
        ])
        values = {
            'cards': cards,
            'page_name': 'consign_cards',
        }
        return request.render(
            'woow_mc_consign.portal_my_consign_cards', values
        )

    @http.route(
        ['/my/consign-cards/<int:card_id>'],
        type='http', auth='user', website=True,
    )
    def portal_consign_card_detail(self, card_id, **kw):
        partner = request.env.user.partner_id
        card = request.env['loyalty.card'].sudo().search([
            ('id', '=', card_id),
            ('partner_id', '=', partner.id),
            ('is_consign', '=', True),
        ], limit=1)
        if not card:
            return request.redirect('/my/consign-cards')
        values = {
            'card': card,
            'page_name': 'consign_card_detail',
        }
        values = self._get_page_view_values(
            card, card._portal_ensure_token(), values, 'my_consign_cards_history', False, **kw,
        )
        return request.render(
            'woow_mc_consign.portal_consign_card_detail', values
        )

    @http.route(
        ['/my/consign-redemptions/<int:redemption_id>'],
        type='http', auth='user', website=True,
    )
    def portal_consign_redemption_detail(self, redemption_id, **kw):
        partner = request.env.user.partner_id
        redemption = request.env['loyalty.consign.redemption'].sudo().search([
            ('id', '=', redemption_id),
            ('card_id.partner_id', '=', partner.id),
        ], limit=1)
        if not redemption:
            return request.redirect('/my/consign-cards')
        values = {
            'redemption': redemption,
            'page_name': 'consign_redemption_detail',
        }
        values = self._get_page_view_values(
            redemption, redemption._portal_ensure_token(), values, 'my_consign_redemptions_history', False, **kw,
        )
        return request.render(
            'woow_mc_consign.portal_consign_redemption_detail', values
        )
