from datetime import date

from odoo import fields, http
from odoo.http import request
from odoo.addons.woow_member_center.controllers.portal import MemberCenterPortal


class MembershipPortal(MemberCenterPortal):

    def _own_member_lines(self, partner=None):
        partner = partner or request.env.user.partner_id
        return request.env['membership.membership_line'].sudo().search([
            ('partner', '=', partner.id),
            ('company_id', 'in', [False, request.website.company_id.id]),
        ], order='date_from desc')

    def _own_membership_state(self, partner, member_lines):
        """Apply Odoo's membership state order to this website's lines only."""
        while partner.associate_member:
            partner = partner.associate_member
            member_lines = self._own_member_lines(partner)

        today = fields.Date.today()
        state = 'none'
        # Core's partner.member_lines uses the model order (newest id first).
        for line in member_lines.sorted(key=lambda line: line.id, reverse=True):
            start, end = line.date_from or date.min, line.date_to or date.min
            if start <= today <= end:
                state = line.state
                break
            if start < today and end <= today and start < end:
                invoice = line.account_invoice_id
                if invoice and invoice.payment_state in ('in_payment', 'paid'):
                    state = 'old'
                elif invoice and invoice.state == 'cancel':
                    state = 'canceled'
                break
        if partner.free_member and state != 'paid':
            return 'free'
        return state

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
