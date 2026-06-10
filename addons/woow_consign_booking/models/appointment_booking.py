# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AppointmentBooking(models.Model):
    _inherit = 'appointment.booking'

    # ── 寄品卡追蹤欄位 ──
    consign_line_id = fields.Many2one(
        'loyalty.consign.line',
        string='寄品明細',
        ondelete='set null',
        copy=False,
    )
    consign_card_id = fields.Many2one(
        'loyalty.card',
        string='寄品卡',
        related='consign_line_id.card_id',
        store=True,
    )
    consign_reserved_qty = fields.Float(
        '已預留數量',
        default=0.0,
        copy=False,
    )
    consign_redemption_id = fields.Many2one(
        'loyalty.consign.redemption',
        string='核銷單',
        ondelete='set null',
        copy=False,
    )
    consign_enabled = fields.Boolean(
        related='appointment_type_id.consign_enabled',
    )

    # ── 建立預約時預留 ──

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for booking in records:
            if booking.appointment_type_id.consign_enabled:
                booking._consign_reserve()
        return records

    def _consign_reserve(self):
        """預約建立時：查卡 → 檢查餘額 → 預留"""
        self.ensure_one()
        apt = self.appointment_type_id
        if not apt.consign_enabled:
            return

        partner = self.partner_id
        if not partner:
            raise UserError('寄品扣點預約需要選擇客戶。')

        program = apt.consign_program_id
        product = apt.consign_product_id
        qty_needed = apt.consign_qty

        # 查卡
        card = self.env['loyalty.card'].sudo().search([
            ('program_id', '=', program.id),
            ('partner_id', '=', partner.id),
            ('is_consign', '=', True),
            ('active', '=', True),
        ], limit=1)
        if not card:
            raise UserError(
                f'客戶「{partner.name}」沒有方案「{program.name}」的寄品卡。'
            )

        # 查品項
        consign_line = card.consign_line_ids.filtered(
            lambda l: l.product_id == product and l.state == 'active'
        )[:1]
        if not consign_line:
            raise UserError(
                f'客戶的寄品卡中沒有品項「{product.display_name}」。'
            )

        # DB lock → 檢查 → 預留
        self.env.cr.execute(
            "SELECT id FROM loyalty_consign_line WHERE id = %s FOR UPDATE",
            [consign_line.id],
        )
        consign_line.invalidate_recordset(
            ['qty_remaining', 'reserved_qty', 'qty_available'])

        available = consign_line.qty_remaining - consign_line.reserved_qty
        if available < qty_needed:
            raise UserError(
                f'寄品卡餘額不足：品項「{product.display_name}」'
                f'可用 {available}，需要 {qty_needed}。'
            )

        consign_line.sudo().write({
            'reserved_qty': consign_line.reserved_qty + qty_needed,
        })
        self.write({
            'consign_line_id': consign_line.id,
            'consign_reserved_qty': qty_needed,
        })
        _logger.info(
            'consign_reserve: booking %s reserved %.1f of %s (card %s)',
            self.name, qty_needed, product.display_name, card.id,
        )

    # ── 確認預約時扣除 ──

    def action_confirm(self):
        for booking in self:
            if booking.consign_line_id and booking.consign_reserved_qty > 0:
                booking._consign_deduct()
        return super().action_confirm()

    def _consign_deduct(self):
        """確認預約：建立核銷單 → 釋放預留"""
        self.ensure_one()
        consign_line = self.consign_line_id
        qty = self.consign_reserved_qty
        card = consign_line.card_id

        redemption = self.env['loyalty.consign.redemption'].create({
            'card_id': card.id,
            'staff_user_id': self.env.user.id,
            'service_note': f'預約扣點: {self.name}',
            'line_ids': [(0, 0, {
                'consign_line_id': consign_line.id,
                'qty_redeemed': qty,
                'note': f'預約 {self.name}',
            })],
        })
        redemption.action_done()

        # 釋放預留
        self.env.cr.execute(
            "SELECT id FROM loyalty_consign_line WHERE id = %s FOR UPDATE",
            [consign_line.id],
        )
        consign_line.invalidate_recordset(['reserved_qty'])
        consign_line.sudo().write({
            'reserved_qty': max(0, consign_line.reserved_qty - qty),
        })

        self.write({
            'consign_redemption_id': redemption.id,
            'consign_reserved_qty': 0,
        })
        _logger.info(
            'consign_deduct: booking %s redeemed %.1f (redemption %s)',
            self.name, qty, redemption.name,
        )

    # ── 取消預約時釋放 ──

    def action_cancel(self):
        for booking in self:
            if booking.consign_line_id:
                booking._consign_release_or_block()
        return super().action_cancel()

    def _consign_release_or_block(self):
        """取消/重置：釋放預留或阻擋已核銷的取消"""
        self.ensure_one()

        # 已核銷 → 阻擋
        if self.consign_redemption_id and self.consign_redemption_id.state == 'done':
            raise UserError(
                f'預約「{self.name}」已完成寄品核銷'
                f'（核銷單：{self.consign_redemption_id.name}），'
                f'無法取消。請聯繫管理員手動處理。'
            )

        # 草稿核銷 → 刪除
        if self.consign_redemption_id and self.consign_redemption_id.state == 'draft':
            self.consign_redemption_id.unlink()

        # 有預留 → 釋放
        if self.consign_reserved_qty > 0 and self.consign_line_id:
            consign_line = self.consign_line_id
            self.env.cr.execute(
                "SELECT id FROM loyalty_consign_line WHERE id = %s FOR UPDATE",
                [consign_line.id],
            )
            consign_line.invalidate_recordset(['reserved_qty'])
            consign_line.sudo().write({
                'reserved_qty': max(0, consign_line.reserved_qty - self.consign_reserved_qty),
            })

        self.write({
            'consign_line_id': False,
            'consign_reserved_qty': 0,
            'consign_redemption_id': False,
        })

    # ── 重置草稿 ──

    def action_draft(self):
        for booking in self:
            if booking.consign_line_id:
                booking._consign_release_or_block()
        return super().action_draft()
