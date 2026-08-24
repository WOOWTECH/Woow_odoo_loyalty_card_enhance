import hashlib
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare


_PROJECTION_CREATE_CONTEXT_KEY = '_woow_consign_projection_create_token'
_PROJECTION_CREATE_TOKEN = object()


class LoyaltyConsignLine(models.Model):
    """Authoritative aggregate projection of the immutable movement ledger."""

    _name = 'loyalty.consign.line'
    _description = '寄品明細'
    _order = 'date_deposited desc, id desc'
    _rec_name = 'product_desc'
    _check_company_auto = True

    card_id = fields.Many2one(
        'loyalty.card', string='寄品卡', required=True, ondelete='cascade',
        index=True, check_company=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='card_id.company_id', store=True, string='公司', index=True,
    )
    partner_id = fields.Many2one(
        related='card_id.partner_id', store=True, string='客戶', index=True,
    )
    program_id = fields.Many2one(
        related='card_id.program_id', store=True, string='寄品方案', index=True,
    )
    currency_id = fields.Many2one(related='card_id.currency_id', store=True)
    product_id = fields.Many2one(
        'product.product', string='品項', required=True, check_company=True,
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='計量單位', required=True, index=True, readonly=True,
        default=lambda self: self.env.ref('uom.product_uom_unit'),
    )
    product_desc = fields.Char(string='品項說明', readonly=True)
    lot_id = fields.Many2one(
        'stock.lot', string='批號', check_company=True, readonly=True,
    )
    storage_note = fields.Char(string='儲位備註', readonly=True)
    qty_deposited = fields.Float(string='存入數量', default=1.0, readonly=True)
    qty_redeemed = fields.Float(string='已核銷數量', readonly=True)
    qty_remaining = fields.Float(
        string='剩餘數量（不含保留）', readonly=True,
        help='Posted movement balance before active Hold allocations.',
    )
    unit_price = fields.Float(string='單價', readonly=True)
    amount_deposited = fields.Monetary(
        string='存入金額', currency_field='currency_id', readonly=True,
    )
    amount_remaining = fields.Monetary(
        string='剩餘金額（不含保留）', currency_field='currency_id', readonly=True,
    )
    date_deposited = fields.Date(string='存入日期', readonly=True)
    sale_line_id = fields.Many2one(
        'sale.order.line', string='來源訂單行', ondelete='set null',
        check_company=True, readonly=True,
    )
    sale_order_id = fields.Many2one(
        related='sale_line_id.order_id', store=True, string='來源訂單',
    )
    is_cancelled = fields.Boolean(string='已取消', default=False, readonly=True)
    state = fields.Selection(
        [('active', '有效'), ('depleted', '已用完'), ('cancelled', '已取消')],
        string='狀態', default='active', readonly=True,
    )
    redemption_line_ids = fields.One2many(
        'loyalty.consign.redemption.line', 'consign_line_id', string='核銷紀錄',
    )
    movement_ids = fields.One2many(
        'loyalty.consign.movement', 'aggregate_line_id', string='Ledger Movements',
        copy=False,
    )
    qty_issued = fields.Float(string='Issued', readonly=True)
    qty_reversed = fields.Float(string='Reversed', readonly=True)
    qty_revoked = fields.Float(string='Revoked', readonly=True)
    qty_on_hold = fields.Float(string='On Hold', readonly=True)
    qty_available = fields.Float(string='Available', readonly=True)

    _sql_constraints = [
        (
            'card_product_uom_unique',
            'unique(card_id, product_id, product_uom_id)',
            'A card has only one aggregate projection per product and UoM.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # Public compatibility creation is temporarily manager-only until the
        # formal manual adjustment command arrives in Task 9. This check must
        # precede every lookup, sudo, row lock, or private mutation.
        if (
            not self.env.is_superuser()
            and not self.env.user.has_group('sales_team.group_sale_manager')
        ):
            raise AccessError(
                _('Only Sales managers may create consignment projections.')
            )
        if self.env.context.get(_PROJECTION_CREATE_CONTEXT_KEY) is _PROJECTION_CREATE_TOKEN:
            return super().create(vals_list)
        created = self.browse()
        for original in vals_list:
            vals = dict(original)
            product = self.env['product.product'].browse(vals.get('product_id'))
            card = self.env['loyalty.card'].browse(vals.get('card_id'))
            if not product.exists() or not card.exists():
                raise ValidationError('A consignment card and product are required.')
            uom = (
                self.env['uom.uom'].browse(vals['product_uom_id'])
                if vals.get('product_uom_id') else product.uom_id
            )
            if not uom.exists():
                raise ValidationError('The consignment projection UoM must exist.')
            quantity = vals.get('qty_deposited', 1.0)
            if float_compare(quantity, 0.0, precision_rounding=uom.rounding) <= 0:
                raise ValidationError('存入數量必須大於 0。')
            line = self._get_or_create_projection(
                card=card, product=product, product_uom=uom,
                metadata={
                    'product_desc_snapshot': vals.get('product_desc') or product.display_name,
                    'storage_snapshot': vals.get('storage_note'),
                    'lot_id': vals.get('lot_id'),
                    'unit_value': product.list_price,
                    'date_deposited': vals.get('date_deposited'),
                    'sale_line_id': vals.get('sale_line_id'),
                },
            )
            self.env['loyalty.consign.movement']._append_movement(
                aggregate_line=line,
                movement_type='issue',
                quantity=quantity,
                source_channel='manual',
                source_model='loyalty.consign.line',
                source_res_id=line.id,
                source_name=vals.get('product_desc') or product.display_name,
                idempotency_key=f'consign:manual-projection:v2:{line.id}:{uuid.uuid4().hex}',
                unit_value=product.list_price,
                product_desc_snapshot=vals.get('product_desc') or product.display_name,
                storage_snapshot=vals.get('storage_note'),
                lot_snapshot=(self.env['stock.lot'].browse(vals['lot_id']).name
                              if vals.get('lot_id') else None),
            )
            created |= line
        return created

    @api.model
    def _get_or_create_projection(self, *, card, product, product_uom, metadata=None):
        card.ensure_one()
        product.ensure_one()
        product_uom.ensure_one()
        if not card.is_consign or not card.partner_id:
            raise ValidationError('A projection requires a consignment card with an owner.')
        if product.uom_id.category_id != product_uom.category_id:
            raise ValidationError('品項與計量單位必須屬於相同類別。')
        company = card.company_id
        if not company or card.program_id.company_id != company:
            raise ValidationError(
                'A projection requires a card and program in one explicit company.'
            )
        if product.company_id and product.company_id != company:
            raise ValidationError('The projection product belongs to another company.')
        self.env.cr.execute(
            'SELECT id FROM loyalty_card WHERE id = %s FOR UPDATE', (card.id,),
        )
        line = self.sudo().search([
            ('card_id', '=', card.id),
            ('product_id', '=', product.id),
            ('product_uom_id', '=', product_uom.id),
        ], limit=1)
        if line:
            return self.browse(line.ids)
        metadata = metadata or {}
        vals = {
            'card_id': card.id,
            'product_id': product.id,
            'product_uom_id': product_uom.id,
            'product_desc': metadata.get('product_desc_snapshot') or product.display_name,
            'qty_deposited': metadata.get('initial_quantity', 0.0),
            'unit_price': metadata.get('unit_value', product.list_price),
            'date_deposited': metadata.get('date_deposited') or fields.Date.context_today(self),
            'storage_note': metadata.get('storage_snapshot'),
            'lot_id': metadata.get('lot_id'),
            'sale_line_id': metadata.get('sale_line_id'),
        }
        line = self.with_context(**{
            _PROJECTION_CREATE_CONTEXT_KEY: _PROJECTION_CREATE_TOKEN,
        }).sudo().create(vals)
        return self.browse(line.ids)

    @api.model
    def _create_for_specific_movement(self, vals):
        product = self.env['product.product'].browse(vals['product_id'])
        return self._get_or_create_projection(
            card=self.env['loyalty.card'].browse(vals['card_id']),
            product=product,
            product_uom=self.env['uom.uom'].browse(
                vals.get('product_uom_id') or product.uom_id.id
            ),
            metadata={
                'product_desc_snapshot': vals.get('product_desc'),
                'storage_snapshot': vals.get('storage_note'),
                'lot_id': vals.get('lot_id'),
                'unit_value': product.list_price,
                'date_deposited': vals.get('date_deposited'),
                'sale_line_id': vals.get('sale_line_id'),
                'initial_quantity': vals.get('qty_deposited', 0.0),
            },
        )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
            self.product_desc = self.product_desc or self.product_id.name
            self.unit_price = self.unit_price or self.product_id.list_price

    @api.constrains('product_id', 'product_uom_id')
    def _check_product_uom_category(self):
        for line in self:
            if line.product_id.uom_id.category_id != line.product_uom_id.category_id:
                raise ValidationError('品項與計量單位必須屬於相同類別。')

    def _projection_expected_rows(self):
        if not self:
            return {}
        self.env.flush_all()
        self.env.cr.execute(
            '''
            WITH movement AS (
                SELECT aggregate_line_id AS line_id,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'issue'), 0) issued,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'redeem'), 0) redeemed,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'redeem_reversal'), 0) reversed,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'issue_reversal'), 0) revoked,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'adjustment_in'), 0) adjusted_in,
                       COALESCE(SUM(quantity) FILTER (WHERE movement_type = 'adjustment_out'), 0) adjusted_out,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'issue'), 0) issued_value,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'redeem'), 0) redeemed_value,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'redeem_reversal'), 0) reversed_value,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'issue_reversal'), 0) revoked_value,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'adjustment_in'), 0) adjusted_in_value,
                       COALESCE(SUM(value_delta) FILTER (WHERE movement_type = 'adjustment_out'), 0) adjusted_out_value,
                       MIN(occurred_at)::date deposited_date,
                       CASE WHEN COUNT(*) FILTER (WHERE movement_type = 'issue') > 0
                                  AND COUNT(*) FILTER (WHERE movement_type = 'issue' AND source_model = 'sale.order.line')
                                      = COUNT(*) FILTER (WHERE movement_type = 'issue')
                                  AND COUNT(DISTINCT source_res_id) FILTER (WHERE movement_type = 'issue') = 1
                            THEN MIN(source_res_id) FILTER (WHERE movement_type = 'issue') END sale_line_id,
                       CASE WHEN COUNT(DISTINCT product_desc_snapshot) FILTER (WHERE movement_type = 'issue') = 1
                            THEN MIN(product_desc_snapshot) FILTER (WHERE movement_type = 'issue') END product_desc,
                       CASE WHEN COUNT(DISTINCT lot_snapshot)
                                      FILTER (WHERE movement_type = 'issue' AND lot_snapshot IS NOT NULL) = 1
                                  AND COUNT(*) FILTER (WHERE movement_type = 'issue' AND lot_snapshot IS NOT NULL)
                                      = COUNT(*) FILTER (WHERE movement_type = 'issue')
                            THEN MIN(lot_snapshot) FILTER (WHERE movement_type = 'issue') END lot_snapshot,
                       CASE WHEN COUNT(DISTINCT storage_snapshot)
                                      FILTER (WHERE movement_type = 'issue' AND storage_snapshot IS NOT NULL) = 1
                                  AND COUNT(*) FILTER (WHERE movement_type = 'issue' AND storage_snapshot IS NOT NULL)
                                      = COUNT(*) FILTER (WHERE movement_type = 'issue')
                            THEN MIN(storage_snapshot) FILTER (WHERE movement_type = 'issue') END storage_note
                  FROM loyalty_consign_movement
                 WHERE aggregate_line_id = ANY(%s)
              GROUP BY aggregate_line_id
            ), active_hold AS (
                SELECT allocation.aggregate_line_id AS line_id,
                       COALESCE(SUM(allocation.quantity), 0) held
                  FROM loyalty_consign_hold_allocation allocation
                  JOIN loyalty_consign_hold hold ON hold.id = allocation.hold_id
                 WHERE hold.state = 'active'
                   AND allocation.aggregate_line_id = ANY(%s)
              GROUP BY allocation.aggregate_line_id
            )
            SELECT line.id, COALESCE(m.issued, 0), COALESCE(m.redeemed, 0),
                   COALESCE(m.reversed, 0), COALESCE(m.revoked, 0),
                   COALESCE(h.held, 0),
                   COALESCE(m.issued + m.reversed + m.adjusted_in - m.redeemed - m.revoked - m.adjusted_out, 0) remaining,
                   COALESCE(m.issued + m.reversed + m.adjusted_in - m.redeemed - m.revoked - m.adjusted_out - COALESCE(h.held, 0), 0) available,
                   COALESCE(m.issued_value, 0),
                   COALESCE(m.issued_value + m.reversed_value + m.adjusted_in_value - m.redeemed_value - m.revoked_value - m.adjusted_out_value, 0) remaining_value,
                   CASE WHEN COALESCE(m.issued, 0) <> 0 THEN m.issued_value / m.issued ELSE 0 END unit_price,
                   m.deposited_date, m.sale_line_id, m.product_desc,
                   m.lot_snapshot, m.storage_note, line.product_id,
                   CASE WHEN COALESCE(m.issued + m.reversed + m.adjusted_in - m.redeemed - m.revoked - m.adjusted_out, 0) <= 0
                                  AND COALESCE(m.revoked, 0) > 0 THEN 'cancelled'
                        WHEN COALESCE(m.issued + m.reversed + m.adjusted_in - m.redeemed - m.revoked - m.adjusted_out, 0) <= 0 THEN 'depleted'
                        ELSE 'active' END state
              FROM loyalty_consign_line line
         LEFT JOIN movement m ON m.line_id = line.id
         LEFT JOIN active_hold h ON h.line_id = line.id
             WHERE line.id = ANY(%s)
          ORDER BY line.id
            ''',
            (self.ids, self.ids, self.ids),
        )
        keys = (
            'id', 'qty_issued', 'qty_redeemed', 'qty_reversed', 'qty_revoked',
            'qty_on_hold', 'qty_remaining', 'qty_available', 'amount_deposited',
            'amount_remaining', 'unit_price', 'date_deposited', 'sale_line_id',
            'product_desc', 'lot_snapshot', 'storage_note', 'product_id', 'state',
        )
        rows = {row[0]: dict(zip(keys, row)) for row in self.env.cr.fetchall()}
        valid_sale_line_ids = set(self.env['sale.order.line'].sudo().search([
            ('id', 'in', [row['sale_line_id'] for row in rows.values()
                          if row['sale_line_id']]),
        ]).ids)
        lot_names = [row['lot_snapshot'] for row in rows.values()
                     if row['lot_snapshot']]
        lots_by_key = {}
        if lot_names:
            lots = self.env['stock.lot'].sudo().search([
                ('name', 'in', lot_names),
                ('product_id', 'in', [row['product_id'] for row in rows.values()]),
            ])
            for lot in lots:
                lots_by_key.setdefault((lot.name, lot.product_id.id), []).append(lot.id)
        for row in rows.values():
            row['qty_deposited'] = row['qty_issued']
            if row['sale_line_id'] not in valid_sale_line_ids:
                row['sale_line_id'] = False
            matching_lots = lots_by_key.get(
                (row['lot_snapshot'], row['product_id']), [],
            )
            row['lot_id'] = matching_lots[0] if len(matching_lots) == 1 else False
        return rows

    def _reconcile_projection(self, recompute_cards=True):
        lines = self.sudo().exists().sorted('id')
        expected = lines._projection_expected_rows()
        for line in lines:
            row = expected[line.id]
            # psycopg2 adapts ``False`` as SQL boolean, which cannot be
            # compared with the integer ``sale_order_line.id``.  Use NULL for
            # an absent optional relation in the raw reconciliation query.
            sale_line_id = row['sale_line_id'] or None
            lot_id = row['lot_id'] or None
            self.env.cr.execute(
                '''
                UPDATE loyalty_consign_line
                   SET qty_issued = %s, qty_deposited = %s, qty_redeemed = %s,
                       qty_reversed = %s, qty_revoked = %s, qty_on_hold = %s,
                       qty_remaining = %s, qty_available = %s,
                       amount_deposited = %s, amount_remaining = %s,
                       unit_price = %s, date_deposited = %s, sale_line_id = %s,
                       sale_order_id = (SELECT order_id FROM sale_order_line WHERE id = %s),
                       product_desc = %s, lot_id = %s, storage_note = %s,
                       state = %s,
                       is_cancelled = (%s = 'cancelled'), write_date = NOW()
                 WHERE id = %s
                ''',
                (
                    row['qty_issued'], row['qty_issued'], row['qty_redeemed'],
                    row['qty_reversed'], row['qty_revoked'], row['qty_on_hold'],
                    row['qty_remaining'], row['qty_available'],
                    row['amount_deposited'], row['amount_remaining'], row['unit_price'],
                    row['date_deposited'], sale_line_id, sale_line_id,
                    row['product_desc'], lot_id, row['storage_note'], row['state'],
                    row['state'], line.id,
                ),
            )
        lines.invalidate_recordset()
        cards = lines.mapped('card_id')
        cards.invalidate_recordset()
        if recompute_cards:
            cards._compute_consign_totals()
        return lines

    def _assert_projection_consistent(self):
        expected = self.sudo()._projection_expected_rows()
        numeric = (
            'qty_issued', 'qty_deposited', 'qty_redeemed', 'qty_reversed',
            'qty_revoked', 'qty_on_hold', 'qty_remaining', 'qty_available',
            'amount_deposited', 'amount_remaining', 'unit_price',
        )
        for line in self:
            row = expected[line.id]
            for field_name in numeric:
                rounding = (
                    line.currency_id.rounding if field_name.startswith('amount_')
                    else line.product_uom_id.rounding
                )
                if float_compare(
                    getattr(line, field_name), row[field_name] or 0.0,
                    precision_rounding=rounding,
                ) != 0:
                    raise ValidationError(
                        f'Projection {line.id} is inconsistent in {field_name}.'
                    )
            for field_name in ('state', 'sale_line_id', 'lot_id', 'date_deposited',
                               'product_desc', 'storage_note'):
                actual = getattr(line, field_name)
                actual = actual.id if isinstance(actual, models.BaseModel) else actual
                if (actual or False) != (row[field_name] or False):
                    raise ValidationError(
                        f'Projection {line.id} is inconsistent in {field_name}.'
                    )
            if line.is_cancelled != (row['state'] == 'cancelled'):
                raise ValidationError(
                    f'Projection {line.id} is inconsistent in is_cancelled.'
                )
        return True

    def action_repair_projection(self):
        self.check_access('read')
        if not self.env.is_superuser() and not self.env.user.has_group(
            'sales_team.group_sale_manager'
        ):
            raise AccessError(_('Only Sales managers may repair consignment projections.'))
        lines = self.sudo().exists().sorted(
            lambda line: (line.company_id.id, line.card_id.id, line.product_id.id,
                          line.product_uom_id.id, line.id)
        )
        for line in lines:
            digest = hashlib.sha256(
                f'{line.company_id.id}:{line.card_id.id}:{line.product_id.id}:{line.product_uom_id.id}'.encode()
            ).digest()
            self.env.cr.execute(
                'SELECT pg_advisory_xact_lock(%s)',
                (int.from_bytes(digest[:8], 'big', signed=True),),
            )
        if lines:
            self.env.cr.execute(
                'SELECT id FROM loyalty_consign_line WHERE id = ANY(%s) ORDER BY id FOR UPDATE',
                (lines.ids,),
            )
        lines._reconcile_projection(recompute_cards=False)
        lines._assert_projection_consistent()
        return True

    def write(self, vals):
        protected = {
            'card_id', 'company_id', 'partner_id', 'program_id', 'currency_id',
            'product_id', 'product_uom_id', 'product_desc', 'lot_id',
            'storage_note', 'qty_deposited', 'qty_redeemed', 'qty_remaining',
            'unit_price', 'amount_deposited', 'amount_remaining', 'date_deposited',
            'sale_line_id', 'sale_order_id', 'is_cancelled', 'state', 'qty_issued',
            'qty_reversed', 'qty_revoked', 'qty_on_hold', 'qty_available',
        }
        if protected & set(vals):
            raise ValidationError(
                'Consignment projections are read-only and are rebuilt from the ledger.'
            )
        # Installed compatibility bridges retain independent historical fields
        # (for example booking ``reserved_qty``). They are not ledger
        # projections and may still be maintained under their own ACLs.
        return super().write(vals)

    def _write_accumulate(self, vals):
        raise ValidationError('Incremental consignment counters are no longer supported.')

    def _write_schema_backfill(self, vals):
        raise ValidationError('Projection schema backfill is only allowed in versioned migration.')

    def unlink(self):
        if self.mapped('movement_ids'):
            raise ValidationError('A consignment projection with movements cannot be deleted.')
        return super().unlink()

    def _append_issue_reversal_for_remaining(
        self, *, source_channel, key_prefix, allow_inactive_card=False,
    ):
        self.ensure_one()
        active_allocations = self.sudo().movement_ids.mapped(
            'hold_allocation_ids'
        ).filtered(lambda allocation: allocation.hold_id.state == 'active')
        if active_allocations:
            raise ValidationError(
                'A consignment line with an active Hold allocation cannot be cancelled or reversed.'
            )
        movement_model = self.env['loyalty.consign.movement']
        issue_states = movement_model._fifo_issue_availability(
            self, include_active_holds=False,
        )
        appended = movement_model.browse()
        sequence = 0
        for state in issue_states:
            issue = state['issue']
            reversible = state['available']
            if float_compare(
                reversible, 0.0, precision_rounding=self.product_uom_id.rounding,
            ) <= 0:
                continue
            sequence += 1
            appended |= movement_model._append_movement(
                aggregate_line=self,
                movement_type='issue_reversal',
                quantity=reversible,
                source_channel=source_channel,
                source_model='loyalty.consign.line',
                source_res_id=self.id,
                source_name=self.display_name,
                idempotency_key=f'{key_prefix}:{sequence}:{issue.id}',
                original_movement=issue,
                allow_inactive_card=allow_inactive_card,
            )
        return appended

    def action_cancel(self):
        # Cancellation is an explicit append-only engine action, not generic
        # projection write access.  Managers need only read visibility here;
        # the group check below is the mutation authority.
        self.check_access('read')
        if not self.env.is_superuser() and not self.env.user.has_group(
            'sales_team.group_sale_manager'
        ):
            raise AccessError(_('Only Sales managers may cancel consignment lines.'))
        for line in self.filtered(lambda item: item.state != 'cancelled'):
            if float_compare(
                line.qty_available, 0.0,
                precision_rounding=line.product_uom_id.rounding,
            ) <= 0:
                raise ValidationError('已無剩餘數量可取消。')
            line._append_issue_reversal_for_remaining(
                source_channel='manual',
                key_prefix=f'consign:legacy-cancellation:v1:{line.id}',
            )
