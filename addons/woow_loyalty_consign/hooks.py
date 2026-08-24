import logging

from odoo import fields


_logger = logging.getLogger(__name__)
_TRIGGER_NAME = 'woow_loyalty_consign_movement_immutable_trg'
_FUNCTION_NAME = 'woow_loyalty_consign_movement_immutable_guard'


def ensure_movement_immutability_trigger(cr):
    """Install the unconditional database guard on clean install and update."""
    cr.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION_NAME}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'loyalty.consign.movement rows are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cr.execute(
        """
        SELECT 1
          FROM pg_trigger
         WHERE tgname = %s
           AND tgrelid = 'loyalty_consign_movement'::regclass
           AND NOT tgisinternal
        """,
        (_TRIGGER_NAME,),
    )
    if not cr.fetchone():
        cr.execute(
            f"""
            CREATE TRIGGER {_TRIGGER_NAME}
            BEFORE UPDATE OR DELETE ON loyalty_consign_movement
            FOR EACH ROW EXECUTE FUNCTION {_FUNCTION_NAME}()
            """
        )


def drop_movement_immutability_trigger(cr):
    cr.execute("SELECT to_regclass('loyalty_consign_movement')")
    if cr.fetchone()[0]:
        cr.execute(
            f'DROP TRIGGER IF EXISTS {_TRIGGER_NAME} '
            'ON loyalty_consign_movement'
        )
    cr.execute(f'DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()')


def backfill_consign_movements(env):
    """Idempotently shadow all accepted facts from the legacy projection."""
    movement_model = env['loyalty.consign.movement'].sudo()
    lines = env['loyalty.consign.line'].sudo().with_context(active_test=False).search([], order='id')
    # Task 4 reconciliation derives cancellation from reversal facts, so retain
    # the legacy marker before issue backfill rewrites the projection.
    cancelled_line_ids = lines.filtered('is_cancelled').ids
    for line in lines:
        if not line.product_uom_id:
            raise RuntimeError(
                'Consignment projection UoM must be repaired by the versioned migration.'
            )
        if line.sale_line_id:
            source_model = 'sale.order.line'
            source_res_id = line.sale_line_id.id
            source_name = line.sale_order_id.display_name or line.sale_line_id.display_name
            source_channel = 'sale'
            issue_key = f'consign:migration:legacy-line:v1:{line.id}'
        else:
            source_model = 'loyalty.consign.line'
            source_res_id = line.id
            source_name = line.display_name
            source_channel = 'migration'
            issue_key = f'consign:legacy-line:v1:{line.id}:issue'
        if not line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue'
        ):
            movement_model._append_movement(
                aggregate_line=line,
                movement_type='issue',
                quantity=line.qty_deposited,
                source_channel=source_channel,
                source_model=source_model,
                source_res_id=source_res_id,
                source_name=source_name,
                idempotency_key=issue_key,
                occurred_at=fields.Datetime.to_datetime(line.date_deposited),
                allow_inactive_card=True,
            )

    done_redemption_lines = env['loyalty.consign.redemption.line'].sudo().search([
        ('redemption_id.state', '=', 'done'),
        ('qty_redeemed', '>', 0),
    ], order='id')
    for redemption_line in done_redemption_lines:
        line = redemption_line.consign_line_id
        existing = line.movement_ids.filtered(lambda movement: (
            movement.movement_type == 'redeem'
            and movement.source_model == 'loyalty.consign.redemption.line'
            and movement.source_res_id == redemption_line.id
        ))
        if not existing:
            movement_model._append_movement(
                aggregate_line=line,
                movement_type='redeem',
                quantity=redemption_line.qty_redeemed,
                source_channel='migration',
                source_model='loyalty.consign.redemption.line',
                source_res_id=redemption_line.id,
                source_name=redemption_line.redemption_id.display_name,
                idempotency_key=f'consign:legacy-redemption:v1:{redemption_line.id}',
                occurred_at=redemption_line.redemption_id.date_redemption,
                unit_value=redemption_line.unit_price,
                allow_inactive_card=True,
            )

    lines.invalidate_recordset(['movement_ids'])
    for line in lines.browse(cancelled_line_ids):
        line._append_issue_reversal_for_remaining(
            source_channel='migration',
            key_prefix=f'consign:legacy-cancellation:v1:{line.id}',
            allow_inactive_card=True,
        )

    ensure_movement_immutability_trigger(env.cr)
    _logger.info('Consignment movement shadow backfill completed for %s legacy lines.', len(lines))


def post_init_hook(env):
    backfill_consign_movements(env)


def uninstall_hook(env):
    drop_movement_immutability_trigger(env.cr)
