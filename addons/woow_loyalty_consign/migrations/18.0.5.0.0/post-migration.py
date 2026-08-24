from odoo import SUPERUSER_ID, api
from odoo.tools import float_compare

from odoo.addons.woow_loyalty_consign.hooks import (
    backfill_consign_movements,
    ensure_movement_immutability_trigger,
)


def migrate(cr, version):
    """Cut over every consolidated projection to deterministic ledger values."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Task 4 removes mutable source_name from command identity. Re-hash Task 3
    # journals transactionally so their exact private replay remains valid.
    operation_model = env['loyalty.consign.operation'].sudo()
    for operation in operation_model.search([], order='id'):
        identity = dict(operation.payload_json or {})
        identity.pop('source_name', None)
        _canonical, payload_hash = operation_model._canonical_payload(identity)
        cr.execute(
            'UPDATE loyalty_consign_operation SET payload_hash = %s WHERE id = %s',
            (payload_hash, operation.id),
        )
    operation_model.invalidate_model(['payload_hash'])

    # Direct upgrades from pre-Task-3 do not have immutable facts until the
    # target schema is loaded. The backfill is idempotent for Task-3-shaped DBs.
    backfill_consign_movements(env)

    # Normalize completed legacy redemption audit snapshots to the exact
    # rounded immutable facts produced by the backfill. This also converts the
    # former related unit_price column into a durable historical snapshot.
    cr.execute(
        '''
        WITH posted AS (
            SELECT movement.source_res_id AS redemption_line_id,
                   SUM(movement.quantity) AS quantity,
                   SUM(movement.value_delta) AS value
              FROM loyalty_consign_movement movement
             WHERE movement.movement_type = 'redeem'
               AND movement.source_model = 'loyalty.consign.redemption.line'
          GROUP BY movement.source_res_id
        )
        UPDATE loyalty_consign_redemption_line audit
           SET qty_redeemed = posted.quantity,
               unit_price = CASE WHEN posted.quantity <> 0
                                 THEN posted.value / posted.quantity ELSE 0 END,
               subtotal = posted.value
          FROM posted, loyalty_consign_redemption redemption
         WHERE audit.id = posted.redemption_line_id
           AND redemption.id = audit.redemption_id
           AND redemption.state = 'done'
        '''
    )

    # Consolidation can merge cancelled and active legacy rows before a direct
    # upgrade has movements. Preserve the cancelled rows' remaining quantity
    # from the durable old snapshots without restoring source-specific lines.
    cr.execute(
        '''
        SELECT mapping.run_id, mapping.survivor_line_id,
               SUM(COALESCE((mapping.old_snapshot->>'qty_remaining')::numeric, 0))
          FROM loyalty_consign_projection_merge_map mapping
          JOIN loyalty_consign_projection_merge_run run ON run.id = mapping.run_id
         WHERE run.before_movement_count = 0
           AND COALESCE((mapping.old_snapshot->>'is_cancelled')::boolean, FALSE)
      GROUP BY mapping.run_id, mapping.survivor_line_id
        '''
    )
    movement_model = env['loyalty.consign.movement'].sudo()
    for run_id, line_id, cancelled_remaining in cr.fetchall():
        line = env['loyalty.consign.line'].sudo().browse(line_id).exists()
        if not line or not cancelled_remaining:
            continue
        issue = line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue'
        ).sorted(lambda movement: (movement.occurred_at, movement.id))[:1]
        if not issue:
            raise RuntimeError('Direct cancellation migration requires an issue fact.')
        existing_reversal = sum(line.movement_ids.filtered(
            lambda movement: movement.movement_type == 'issue_reversal'
        ).mapped('quantity'))
        quantity = float(cancelled_remaining) - existing_reversal
        if float_compare(
            quantity, 0.0, precision_rounding=line.product_uom_id.rounding,
        ) > 0:
            movement_model._append_movement(
                aggregate_line=line,
                movement_type='issue_reversal',
                quantity=quantity,
                source_channel='migration',
                source_model='loyalty.consign.line',
                source_res_id=line.id,
                source_name=line.display_name,
                idempotency_key=(
                    f'consign:migration:direct-cancellation:v1:{run_id}:{line.id}'
                ),
                original_movement=issue,
                allow_inactive_card=True,
            )

    lines = env['loyalty.consign.line'].sudo().with_context(active_test=False).search(
        [], order='card_id, product_id, product_uom_id, id',
    )
    lines._reconcile_projection()
    lines._assert_projection_consistent()
    ensure_movement_immutability_trigger(cr)
    cr.execute(
        '''
        SELECT COUNT(*)
          FROM pg_trigger
         WHERE tgname = 'woow_loyalty_consign_movement_immutable_trg'
           AND tgrelid = 'loyalty_consign_movement'::regclass
           AND NOT tgisinternal
        '''
    )
    if cr.fetchone()[0] != 1:
        raise RuntimeError('Movement immutability trigger verification failed.')
