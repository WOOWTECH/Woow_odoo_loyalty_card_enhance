from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round


_AUDIT_WRITE_CONTEXT_KEY = '_woow_consign_redemption_audit_write_token'
_AUDIT_WRITE_TOKEN = object()


class LoyaltyConsignRedemption(models.Model):
    _name = 'loyalty.consign.redemption'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _description = '寄品核銷單'
    _order = 'date_redemption desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='核銷單號', readonly=True, copy=False, default='/',
    )
    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True,
        domain=[('is_consign', '=', True)],
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', store=True, string='客戶',
    )
    date_redemption = fields.Datetime(
        string='核銷日期', default=fields.Datetime.now,
    )
    staff_user_id = fields.Many2one(
        'res.users', string='服務人員', default=lambda self: self.env.user,
    )
    service_note = fields.Text(
        string='服務備註',
        help='施打劑量、操作師、備註等',
    )
    state = fields.Selection(
        [
            ('draft', '草稿'),
            ('done', '已完成'),
        ],
        string='狀態', default='draft', readonly=True, copy=False,
        tracking=True,
    )
    line_ids = fields.One2many(
        'loyalty.consign.redemption.line', 'redemption_id', string='核銷明細',
    )
    total_redeemed_value = fields.Monetary(
        string='核銷總金額', compute='_compute_total_redeemed_value',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='card_id.currency_id', store=True,
    )

    def _compute_access_url(self):
        super()._compute_access_url()
        for rec in self:
            rec.access_url = f'/my/consign-redemptions/{rec.id}'

    @api.depends('line_ids.subtotal')
    def _compute_total_redeemed_value(self):
        for rec in self:
            rec.total_redeemed_value = sum(rec.line_ids.mapped('subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'loyalty.consign.redemption'
                ) or '/'
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            'name', 'card_id', 'date_redemption', 'staff_user_id',
            'service_note', 'state', 'line_ids',
        }
        if (
            self.env.context.get(_AUDIT_WRITE_CONTEXT_KEY) is not _AUDIT_WRITE_TOKEN
            and protected & set(vals)
            and self.filtered(lambda redemption: redemption.state == 'done')
        ):
            raise ValidationError('Completed redemption audit records are immutable.')
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda redemption: redemption.state == 'done'):
            raise ValidationError('Completed redemption audit records cannot be deleted.')
        return super().unlink()

    def action_done(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError('只有草稿狀態的核銷單才能確認。')
            if not rec.line_ids:
                raise ValidationError('核銷單至少需要一筆明細。')

            # Deterministic row locks serialize both duplicate lines in this
            # command and concurrent redemptions. Under REPEATABLE READ, a
            # stale waiter is raised as SerializationFailure for Odoo retry.
            consign_lines = rec.line_ids.mapped('consign_line_id').sorted('id')
            if consign_lines:
                self.env.cr.execute(
                    '''SELECT id FROM loyalty_consign_line
                        WHERE id = ANY(%s) ORDER BY id FOR UPDATE''',
                    (consign_lines.ids,),
                )
                consign_lines.invalidate_recordset([
                    'qty_available', 'qty_redeemed', 'qty_deposited', 'state',
                    'movement_ids',
                ])

            requested_by_line = {}
            rounded_by_redemption_line = {}
            for line in rec.line_ids:
                desc = line.product_desc or line.product_id.name
                if line.consign_line_id.card_id != rec.card_id:
                    raise ValidationError(
                        f'品項「{desc}」不屬於此寄品卡，無法核銷。'
                    )
                if line.consign_line_id.state != 'active':
                    raise ValidationError(
                        f'品項「{desc}」狀態為「{line.consign_line_id.state}」，僅有效品項可核銷。'
                    )
                rounding = line.consign_line_id.product_uom_id.rounding
                rounded = float_round(
                    line.qty_redeemed, precision_rounding=rounding,
                )
                if float_compare(
                    rounded, 0.0, precision_rounding=rounding,
                ) <= 0:
                    raise ValidationError(
                        f'品項「{desc}」的核銷數量必須大於 0。'
                    )
                rounded_by_redemption_line[line.id] = rounded
                requested_by_line[line.consign_line_id.id] = (
                    requested_by_line.get(line.consign_line_id.id, 0.0)
                    + rounded
                )
            for consign_line in consign_lines:
                requested = requested_by_line[consign_line.id]
                if float_compare(
                    requested, consign_line.qty_available,
                    precision_rounding=consign_line.product_uom_id.rounding,
                ) > 0:
                    raise ValidationError(
                        f'品項「{consign_line.product_desc or consign_line.product_id.name}」'
                        f'核銷總數量 ({requested}) 超過可用數量 '
                        f'({consign_line.qty_available})。'
                    )
            movement_model = self.env['loyalty.consign.movement']
            issue_states_by_line = {
                consign_line.id: movement_model._fifo_issue_availability(consign_line)
                for consign_line in consign_lines
            }
            chunks_by_redemption_line = {}
            for line in rec.line_ids.sorted('id'):
                remaining = rounded_by_redemption_line[line.id]
                chunks = []
                for state in issue_states_by_line[line.consign_line_id.id]:
                    quantity = min(remaining, state['available'])
                    if float_compare(
                        quantity, 0.0,
                        precision_rounding=line.product_uom_id.rounding,
                    ) <= 0:
                        continue
                    chunks.append((state['issue'], quantity))
                    state['available'] -= quantity
                    remaining -= quantity
                    if float_compare(
                        remaining, 0.0,
                        precision_rounding=line.product_uom_id.rounding,
                    ) <= 0:
                        break
                if float_compare(
                    remaining, 0.0,
                    precision_rounding=line.product_uom_id.rounding,
                ) > 0:
                    raise ValidationError(
                        f'品項「{line.product_desc or line.product_id.name}」'
                        '沒有足夠的可核銷入帳批次。'
                    )
                chunks_by_redemption_line[line.id] = chunks

            for line in rec.line_ids.sorted('id'):
                posted = movement_model.browse()
                for sequence, (issue, quantity) in enumerate(
                    chunks_by_redemption_line[line.id], start=1,
                ):
                    posted |= movement_model._append_movement(
                        aggregate_line=line.consign_line_id,
                        movement_type='redeem',
                        quantity=quantity,
                        source_channel='manual',
                        source_model='loyalty.consign.redemption.line',
                        source_res_id=line.id,
                        source_name=rec.display_name,
                        idempotency_key=(
                            f'consign:legacy-redemption:v2:{line.id}:'
                            f'{sequence}:{issue.id}'
                        ),
                        occurred_at=rec.date_redemption,
                        original_movement=issue,
                    )
                normalized_quantity = rounded_by_redemption_line[line.id]
                subtotal = line.currency_id.round(sum(posted.mapped('value_delta')))
                line.with_context(**{
                    _AUDIT_WRITE_CONTEXT_KEY: _AUDIT_WRITE_TOKEN,
                }).sudo().write({
                    'qty_redeemed': normalized_quantity,
                    'unit_price': subtotal / normalized_quantity,
                    'subtotal': subtotal,
                })
            rec.write({'state': 'done'})
            rec.card_id.message_post(
                body=f'核銷單 {rec.name} 已完成，共核銷 {len(rec.line_ids)} 筆品項。',
                message_type='notification',
            )


class LoyaltyConsignRedemptionLine(models.Model):
    _name = 'loyalty.consign.redemption.line'
    _description = '寄品核銷明細'
    _rec_name = 'product_desc'

    redemption_id = fields.Many2one(
        'loyalty.consign.redemption', string='核銷單', required=True, ondelete='cascade',
    )
    consign_line_id = fields.Many2one(
        'loyalty.consign.line', string='寄品明細', required=True,
    )
    product_id = fields.Many2one(
        related='consign_line_id.product_id', store=True, string='品項',
    )
    product_uom_id = fields.Many2one(
        related='consign_line_id.product_uom_id', store=True, string='計量單位',
    )
    product_desc = fields.Char(
        related='consign_line_id.product_desc', store=True, string='品項說明',
    )
    qty_available = fields.Float(
        related='consign_line_id.qty_available', string='可用數量',
    )
    qty_redeemed = fields.Float(
        string='本次核銷數量',
    )
    unit_price = fields.Float(
        string='單價', readonly=True, copy=False,
        help='Draft projection price, replaced by the effective ledger value on completion.',
    )
    subtotal = fields.Monetary(
        string='小計', currency_field='currency_id', readonly=True, copy=False,
        help='Authoritative sum of the exact posted redemption movement values.',
    )
    currency_id = fields.Many2one(
        related='redemption_id.currency_id', store=True,
    )
    note = fields.Char(string='備註')

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for original in vals_list:
            vals = dict(original)
            redemption = self.env['loyalty.consign.redemption'].browse(
                vals.get('redemption_id')
            )
            if redemption and redemption.state == 'done':
                raise ValidationError(
                    'Lines cannot be added to a completed redemption audit.'
                )
            consign_line = self.env['loyalty.consign.line'].browse(
                vals.get('consign_line_id')
            )
            unit_price = consign_line.unit_price if consign_line else 0.0
            currency = consign_line.currency_id or self.env.company.currency_id
            vals['unit_price'] = unit_price
            vals['subtotal'] = currency.round(
                vals.get('qty_redeemed', 0.0) * unit_price
            )
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        if (
            self.env.context.get(_AUDIT_WRITE_CONTEXT_KEY) is not _AUDIT_WRITE_TOKEN
            and self.filtered(lambda line: line.redemption_id.state == 'done')
        ):
            raise ValidationError('Completed redemption audit lines are immutable.')
        if 'subtotal' in vals or not ({'qty_redeemed', 'unit_price'} & set(vals)):
            return super().write(vals)
        for line in self:
            line_vals = dict(vals)
            quantity = line_vals.get('qty_redeemed', line.qty_redeemed)
            unit_price = line_vals.get('unit_price', line.unit_price)
            line_vals['subtotal'] = line.currency_id.round(quantity * unit_price)
            super(LoyaltyConsignRedemptionLine, line).write(line_vals)
        return True

    def unlink(self):
        if self.filtered(lambda line: line.redemption_id.state == 'done'):
            raise ValidationError('Completed redemption audit lines cannot be deleted.')
        return super().unlink()

    @api.onchange('consign_line_id', 'qty_redeemed')
    def _onchange_snapshot_value(self):
        for line in self:
            if line.redemption_id.state != 'done':
                line.unit_price = line.consign_line_id.unit_price
                currency = (
                    line.currency_id or line.consign_line_id.currency_id
                    or self.env.company.currency_id
                )
                line.subtotal = currency.round(
                    line.qty_redeemed * line.unit_price
                )

    @api.constrains('qty_redeemed')
    def _check_qty_redeemed(self):
        for line in self:
            if line.qty_redeemed < 0:
                raise ValidationError('核銷數量不可為負數。')
