from odoo import fields, http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class MembershipPortal(MemberCenterPortal):

    def _own_member_lines(self):
        partner = request.env.user.partner_id
        return request.env['membership.membership_line'].sudo().search([
            ('partner', '=', partner.id),
            ('company_id', 'in', [False, request.env.user.company_id.id]),
        ], order='date_from desc')

    def _own_membership_state(self, partner, member_lines):
        """Keep another company's membership status out of this portal."""
        today = fields.Date.today()
        current = member_lines.filtered(
            lambda line: (not line.date_from or line.date_from <= today)
            and (not line.date_to or line.date_to >= today)
        )
        if current:
            return current[0].state
        if member_lines:
            if any(line.state == 'paid' for line in member_lines):
                return 'old'
            return member_lines[0].state
        return 'free' if partner.free_member else 'none'

    def _prepare_hub_values(self):
        values = super()._prepare_hub_values()
        partner = request.env.user.partner_id.sudo()
        membership_state = self._own_membership_state(partner, self._own_member_lines())
        membership_state_label = dict(
            partner.fields_get(['membership_state'])['membership_state']['selection']
        ).get(membership_state, membership_state)
        # Membership always shown when module is installed
        values.update({
            'show_membership': True,
            'membership_state': membership_state,
            'membership_state_label': membership_state_label,
        })
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'member_center_count' in counters:
            member_lines = len(self._own_member_lines())
            values['member_center_count'] = values.get('member_center_count', 0) + member_lines
        return values

    @http.route(
        '/my/member-center/membership',
        type='http', auth='user', website=True,
    )
    def portal_mc_membership(self, **kw):
        partner = request.env.user.partner_id.sudo()
        member_lines = self._own_member_lines()

        membership_state = self._own_membership_state(partner, member_lines)
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
