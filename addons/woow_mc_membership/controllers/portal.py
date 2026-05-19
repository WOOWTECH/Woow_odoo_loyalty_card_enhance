from odoo import http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class MembershipPortal(MemberCenterPortal):

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id.sudo()
        membership_state = partner.membership_state
        membership_state_label = dict(
            partner.fields_get(['membership_state'])['membership_state']['selection']
        ).get(membership_state, membership_state)
        # 會員資格永遠顯示（只要模組安裝就顯示）
        values.update({
            'show_membership': True,
            'membership_state': membership_state,
            'membership_state_label': membership_state_label,
        })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            partner = request.env.user.partner_id
            member_lines = request.env['membership.membership_line'].sudo().search_count([
                ('partner', '=', partner.id),
            ])
            values['member_center_count'] = values.get('member_center_count', 0) + member_lines
        return values

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
        return request.render('woow_mc_membership.portal_mc_membership', values)
