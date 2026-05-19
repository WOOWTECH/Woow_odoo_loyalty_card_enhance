from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class CouponPortal(MemberCenterPortal):

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id
        # 只計算尚未使用的優惠券（points > 0）
        coupon_count = request.env['loyalty.card'].sudo().search_count([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'coupons'),
            ('active', '=', True),
            ('points', '>', 0),
        ])
        if coupon_count:
            values.update({
                'show_coupon': True,
                'coupon_count': coupon_count,
            })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            count = request.env['loyalty.card'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('program_id.program_type', '=', 'coupons'),
                ('active', '=', True),
                ('points', '>', 0),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + count
        return values

    @http.route(
        '/my/member-center/coupons',
        type='http', auth='user', website=True,
    )
    def portal_mc_coupon_list(self, **kw):
        cards = self._get_cards_by_type('coupons')
        values = {
            'page_name': 'mc_coupons',
            'cards': cards,
        }
        return request.render('woow_mc_coupon.portal_mc_coupon_list', values)

    @http.route(
        '/my/member-center/coupons/<int:card_id>',
        type='http', auth='user', website=True,
    )
    def portal_mc_coupon_detail(self, card_id, **kw):
        card = self._get_single_card(card_id, 'coupons')
        if not card:
            return request.redirect('/my/member-center/coupons')
        values = {
            'page_name': 'mc_coupon_detail',
            'card': card,
        }
        values.update(self._get_card_history_values(card, 'coupons'))
        values = self._get_page_view_values(
            card, card._portal_ensure_token(), values, 'my_mc_coupon_history', False, **kw,
        )
        return request.render('woow_mc_coupon.portal_mc_coupon_detail', values)
