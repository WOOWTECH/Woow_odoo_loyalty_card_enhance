import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Backfill balance_before / balance_after for existing loyalty.history records."""
    _logger.info("woow_member_center: backfilling loyalty.history balance columns...")

    histories = env['loyalty.history'].sudo().search(
        [('balance_before', '=', 0), ('balance_after', '=', 0)],
        order='card_id, create_date, id',
    )

    card_groups = {}
    for rec in histories:
        card_groups.setdefault(rec.card_id.id, []).append(rec)

    updated = 0
    for card_id, records in card_groups.items():
        running_balance = 0.0
        for rec in records:
            balance_before = running_balance
            balance_after = running_balance + rec.issued - rec.used
            running_balance = balance_after
            rec.write({
                'balance_before': balance_before,
                'balance_after': balance_after,
            })
            updated += 1

    _logger.info(
        "woow_member_center: backfilled %d loyalty.history records across %d cards.",
        updated, len(card_groups),
    )
