from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


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
            'membership_state': membership_state,
            'membership_state_label': membership_state_label,
            'consign_count': consign_count,
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
