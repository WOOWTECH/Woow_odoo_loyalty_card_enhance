from odoo import _, http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.portal import pager as portal_pager


# Mapping: URL slug → program_type used in loyalty.card
_CARD_TYPE_MAP = {
    'ewallet': 'ewallet',
    'loyalty': 'loyalty',
    'gift-cards': 'gift_card',
    'coupons': 'coupons',
}

# Mapping: URL slug → list page URL (for redirects)
_CARD_LIST_URLS = {
    'ewallet': '/my/member-center/ewallet',
    'loyalty': '/my/member-center/loyalty',
    'gift-cards': '/my/member-center/gift-cards',
    'coupons': '/my/member-center/coupons',
}

# Mapping: URL slug → breadcrumb labels
_CARD_TYPE_LABELS = {
    'ewallet': '電子錢包',
    'loyalty': '集點卡',
    'gift-cards': '禮品卡',
    'coupons': '優惠券',
}

_HISTORY_PAGE_SIZE = 10      # detail page: latest N
_HISTORY_FULL_PAGE_SIZE = 20  # history page: per-page


class MemberCenterPortal(CustomerPortal):

    # ----------------------------------------------------------------
    # Portal Home Counter
    # ----------------------------------------------------------------

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            LoyaltyCard = request.env['loyalty.card'].sudo()

            card_count = LoyaltyCard.search_count([
                ('partner_id', '=', partner.id),
                ('active', '=', True),
            ])
            member_lines = request.env['membership.membership_line'].sudo().search_count([
                ('partner', '=', partner.id),
            ])
            values['member_center_count'] = card_count + member_lines
        return values

    # ----------------------------------------------------------------
    # Helper: query cards by program_type
    # ----------------------------------------------------------------

    def _get_cards_by_type(self, program_type):
        partner = request.env.user.partner_id
        return request.env['loyalty.card'].sudo().search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', program_type),
            ('active', '=', True),
        ])

    def _get_single_card(self, card_id, program_type):
        partner = request.env.user.partner_id
        return request.env['loyalty.card'].sudo().search([
            ('id', '=', card_id),
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', program_type),
            ('active', '=', True),
        ], limit=1)

    # ----------------------------------------------------------------
    # Helper: loyalty.history for detail pages
    # ----------------------------------------------------------------

    def _get_card_history_values(self, card, card_type_slug):
        """Query loyalty.history records for a card and return template values."""
        LoyaltyHistory = request.env['loyalty.history'].sudo()
        domain = [
            ('card_id', '=', card.id),
            '|', ('issued', '!=', 0), ('used', '!=', 0),
        ]
        history_count = LoyaltyHistory.search_count(domain)
        history_lines = LoyaltyHistory.search(
            domain, order='create_date desc', limit=_HISTORY_PAGE_SIZE,
        )
        return {
            'history_lines': history_lines,
            'history_count': history_count,
            'show_history_link': history_count > _HISTORY_PAGE_SIZE,
            'history_url': f'/my/member-center/{card_type_slug}/{card.id}/history',
        }

    # ----------------------------------------------------------------
    # Helper: searchbar sortings for history page
    # ----------------------------------------------------------------

    def _get_history_searchbar_sortings(self):
        return {
            'date': {'label': _("日期"), 'order': 'create_date desc'},
            'issued': {'label': _("增加"), 'order': 'issued desc'},
            'used': {'label': _("使用"), 'order': 'used desc'},
            'description': {'label': _("說明"), 'order': 'description'},
        }

    # ----------------------------------------------------------------
    # Hub Page
    # ----------------------------------------------------------------

    @http.route(
        '/my/member-center',
        type='http', auth='user', website=True,
    )
    def portal_member_center_hub(self, **kw):
        partner = request.env.user.partner_id
        LoyaltyCard = request.env['loyalty.card'].sudo()

        # eWallet
        ewallet_cards = LoyaltyCard.search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'ewallet'),
            ('active', '=', True),
        ])
        ewallet_balance = sum(ewallet_cards.mapped('points'))

        # Loyalty
        loyalty_cards = LoyaltyCard.search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'loyalty'),
            ('active', '=', True),
        ])
        loyalty_points = sum(loyalty_cards.mapped('points'))

        # Gift Card
        gift_cards = LoyaltyCard.search([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'gift_card'),
            ('active', '=', True),
        ])
        gift_balance = sum(gift_cards.mapped('points'))

        # Coupon
        coupon_count = LoyaltyCard.search_count([
            ('partner_id', '=', partner.id),
            ('program_id.program_type', '=', 'coupons'),
            ('active', '=', True),
        ])

        # Membership
        partner_sudo = partner.sudo()
        membership_state = partner_sudo.membership_state
        membership_state_label = dict(
            partner_sudo.fields_get(['membership_state'])['membership_state']['selection']
        ).get(membership_state, membership_state)

        # Consign
        consign_count = LoyaltyCard.search_count([
            ('partner_id', '=', partner.id),
            ('is_consign', '=', True),
            ('active', '=', True),
        ])

        # Currency for monetary display
        currency = request.env.company.currency_id

        values = {
            'page_name': 'member_center',
            'ewallet_balance': ewallet_balance,
            'loyalty_points': loyalty_points,
            'gift_balance': gift_balance,
            'coupon_count': coupon_count,
            'consign_count': consign_count,
            'membership_state': membership_state,
            'membership_state_label': membership_state_label,
            'currency': currency,
            'ewallet_point_name': ewallet_cards[:1].point_name if ewallet_cards else '元',
            'loyalty_point_name': loyalty_cards[:1].point_name if loyalty_cards else '點',
            'gift_point_name': gift_cards[:1].point_name if gift_cards else '元',
        }
        return request.render('woow_member_center.portal_member_center_hub', values)

    # ----------------------------------------------------------------
    # eWallet
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
        return request.render('woow_member_center.portal_mc_ewallet', values)

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
        return request.render('woow_member_center.portal_mc_ewallet_detail', values)

    # ----------------------------------------------------------------
    # Loyalty
    # ----------------------------------------------------------------

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
        return request.render('woow_member_center.portal_mc_loyalty_list', values)

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
        return request.render('woow_member_center.portal_mc_loyalty_detail', values)

    # ----------------------------------------------------------------
    # Gift Card
    # ----------------------------------------------------------------

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
        return request.render('woow_member_center.portal_mc_gift_card_list', values)

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
        return request.render('woow_member_center.portal_mc_gift_card_detail', values)

    # ----------------------------------------------------------------
    # Coupon
    # ----------------------------------------------------------------

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
        return request.render('woow_member_center.portal_mc_coupon_list', values)

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
        return request.render('woow_member_center.portal_mc_coupon_detail', values)

    # ----------------------------------------------------------------
    # Membership
    # ----------------------------------------------------------------

    @http.route(
        '/my/member-center/membership',
        type='http', auth='user', website=True,
    )
    def portal_mc_membership(self, **kw):
        partner = request.env.user.partner_id.sudo()
        member_lines = request.env['membership.membership_line'].sudo().search([
            ('partner', '=', partner.id),
        ], order='date_from desc')

        membership_state = partner.membership_state
        membership_state_label = dict(
            partner.fields_get(['membership_state'])['membership_state']['selection']
        ).get(membership_state, membership_state)

        values = {
            'page_name': 'mc_membership',
            'partner': partner,
            'member_lines': member_lines,
            'membership_state': membership_state,
            'membership_state_label': membership_state_label,
        }
        return request.render('woow_member_center.portal_mc_membership', values)

    # ----------------------------------------------------------------
    # Standalone History Page (pagination + sorting)
    # ----------------------------------------------------------------

    @http.route([
        '/my/member-center/<string:card_type>/<int:card_id>/history',
        '/my/member-center/<string:card_type>/<int:card_id>/history/page/<int:page>',
    ], type='http', auth='user', website=True)
    def portal_mc_card_history(self, card_type, card_id, page=1, sortby='date', **kw):
        program_type = _CARD_TYPE_MAP.get(card_type)
        if not program_type:
            return request.redirect('/my/member-center')

        card = self._get_single_card(card_id, program_type)
        if not card:
            return request.redirect(_CARD_LIST_URLS.get(card_type, '/my/member-center'))

        LoyaltyHistory = request.env['loyalty.history'].sudo()
        domain = [
            ('card_id', '=', card.id),
            '|', ('issued', '!=', 0), ('used', '!=', 0),
        ]

        searchbar_sortings = self._get_history_searchbar_sortings()
        if sortby not in searchbar_sortings:
            sortby = 'date'
        order = searchbar_sortings[sortby]['order']

        history_count = LoyaltyHistory.search_count(domain)
        pager = portal_pager(
            url=f'/my/member-center/{card_type}/{card_id}/history',
            url_args={'sortby': sortby},
            total=history_count,
            page=page,
            step=_HISTORY_FULL_PAGE_SIZE,
        )
        history_lines = LoyaltyHistory.search(
            domain,
            order=order,
            limit=_HISTORY_FULL_PAGE_SIZE,
            offset=pager['offset'],
        )

        values = {
            'page_name': 'mc_card_history',
            'card': card,
            'card_type': card_type,
            'card_type_label': _CARD_TYPE_LABELS.get(card_type, ''),
            'card_url': f'/my/member-center/{card_type}/{card_id}',
            'parent_url': _CARD_LIST_URLS.get(card_type, '/my/member-center'),
            'parent_title': _CARD_TYPE_LABELS.get(card_type, ''),
            'history_lines': history_lines,
            'history_count': history_count,
            'pager': pager,
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'default_url': f'/my/member-center/{card_type}/{card_id}/history',
        }
        return request.render('woow_member_center.portal_mc_card_history', values)
