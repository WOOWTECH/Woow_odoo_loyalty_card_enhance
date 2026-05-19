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
            values['member_center_count'] = 0
        return values

    # ----------------------------------------------------------------
    # Hub Values — 子模組透過 super() 覆寫此方法注入資料
    # ----------------------------------------------------------------

    def _prepare_hub_values(self):
        """Return hub card values. Sub-modules override via super() to inject their data."""
        return {}

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
        values = {
            'page_name': 'member_center',
        }
        values.update(self._prepare_hub_values())
        return request.render('woow_member_center.portal_member_center_hub', values)

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
