#!/usr/bin/env python3
"""Odoo-shell Task 5 real two-cursor authorization probe.

Run against an ephemeral test database only:
  odoo shell -d woow_task5_test < task5_authorize_concurrency_probe.py
Set WOOW_TASK5_PROBE_ALLOW=1 only for another disposable database name.
"""

import os
from datetime import timedelta

from psycopg2.errors import SerializationFailure

from odoo import SUPERUSER_ID, api, fields
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry


db_name = env.cr.dbname
if 'test' not in db_name.lower() and os.environ.get('WOOW_TASK5_PROBE_ALLOW') != '1':
    raise RuntimeError('Task 5 probe refuses a non-test database.')

registry = Registry(db_name)
company_id = env.company.id
partner = env['res.partner'].create({
    'name': 'Task5 Concurrent Owner', 'company_id': company_id,
})
product = env['product.product'].create({
    'name': 'Task5 Concurrent Product', 'type': 'service', 'list_price': 90,
})
program = env['loyalty.program'].create({
    'name': 'Task5 Concurrent Program', 'program_type': 'consign', 'active': True,
    'company_id': company_id, 'currency_id': env.company.currency_id.id,
})
issue = env['loyalty.consign.engine']._issue(
    source=partner, partner=partner, program=program,
    grants=[{'product': product, 'quantity': 10}],
    idempotency_key=f'task5:probe:issue:{partner.id}',
)
partner_id = partner.id
product_id = product.id
card_id = issue.result_json['card_id']
line_id = issue.result_json['projection_ids'][0]
issue_id = issue.movement_ids.id
second_program = env['loyalty.program'].create({
    'name': 'Task5 Concurrent Second Program', 'program_type': 'consign', 'active': True,
    'company_id': company_id, 'currency_id': env.company.currency_id.id,
})
second_issue = env['loyalty.consign.engine']._issue(
    source=partner, partner=partner, program=second_program,
    grants=[{'product': product, 'quantity': 1}],
    idempotency_key=f'task5:probe:second-issue:{partner.id}',
)
second_card_id = second_issue.result_json['card_id']
env.cr.commit()


def authorize(call_env, key, quantity=6):
    return call_env['loyalty.consign.engine']._authorize(
        source=call_env['res.partner'].browse(partner_id),
        partner=call_env['res.partner'].browse(partner_id),
        requests=[{
            'card_id': card_id, 'product_id': product_id,
            'uom_id': call_env['product.product'].browse(product_id).uom_id.id,
            'quantity': quantity,
        }],
        idempotency_key=key,
    )


# Distinct keys: stale loser is fenced on the durable projection tuple. A
# fresh outer retry reports a controlled capacity error, never UniqueViolation.
first_key = f'task5:probe:authorize:first:{partner_id}'
second_key = f'task5:probe:authorize:second:{partner_id}'
stale = registry.cursor()
stale.execute('SELECT count(*) FROM res_partner')
stale.fetchone()
with registry.cursor() as winner_cr:
    winner_env = api.Environment(winner_cr, SUPERUSER_ID, {})
    winner = authorize(winner_env, first_key)
    winner_hold_id = winner.result_json['hold_id']
    winner_cr.commit()
try:
    authorize(api.Environment(stale, SUPERUSER_ID, {}), second_key)
except SerializationFailure:
    stale.rollback()
else:
    raise AssertionError('Distinct-key stale authorization was not fenced.')
finally:
    stale.close()
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    try:
        authorize(retry_env, second_key)
    except ValidationError:
        retry_cr.rollback()
    else:
        raise AssertionError('Fresh retry did not return controlled insufficiency.')
with registry.cursor() as check_cr:
    check_env = api.Environment(check_cr, SUPERUSER_ID, {})
    holds = check_env['loyalty.consign.hold'].search([
        ('id', '=', winner_hold_id), ('state', '=', 'active'),
    ])
    line = check_env['loyalty.consign.line'].browse(line_id)
    assert len(holds) == 1
    assert sum(holds.allocation_line_ids.mapped('quantity')) == 6
    assert line.qty_on_hold == 6 and line.qty_available == 4
    assert check_env['loyalty.consign.hold.allocation'].search_count([
        ('aggregate_line_id', '=', line_id),
    ]) == 1
    check_cr.commit()

# Same key from a stale snapshot is fenced by the durable operation token; its
# fresh retry returns the exact completed operation and Hold.
same_stale = registry.cursor()
same_stale.execute('SELECT count(*) FROM res_partner')
same_stale.fetchone()
same_key = f'task5:probe:authorize:same:{partner_id}'
# Use the available four, then expire it so the later reversal race has room.
with registry.cursor() as same_winner_cr:
    same_env = api.Environment(same_winner_cr, SUPERUSER_ID, {})
    same_winner = authorize(same_env, same_key, quantity=4)
    same_operation_id = same_winner.id
    same_hold_id = same_winner.result_json['hold_id']
    same_winner_cr.commit()
try:
    authorize(api.Environment(same_stale, SUPERUSER_ID, {}), same_key, quantity=4)
except SerializationFailure:
    same_stale.rollback()
else:
    raise AssertionError('Same-key stale replay was not fenced.')
finally:
    same_stale.close()
with registry.cursor() as same_retry_cr:
    same_retry_env = api.Environment(same_retry_cr, SUPERUSER_ID, {})
    replay = authorize(same_retry_env, same_key, quantity=4)
    assert replay.id == same_operation_id
    assert replay.result_json['hold_id'] == same_hold_id
    assert len(replay.hold_ids) == 1
    same_retry_cr.commit()

# A locked earliest Hold cannot starve an unrelated later candidate. The
# batch_size=1 worker skips the locked Hold, expires the other one, then a fresh
# batch_size=1 run expires the formerly locked Hold exactly once and remains idempotent.
with registry.cursor() as expiry_setup_cr:
    expiry_setup_env = api.Environment(expiry_setup_cr, SUPERUSER_ID, {})
    # This command intentionally targets the second card to keep dimensions unrelated.
    expiry_other = expiry_setup_env['loyalty.consign.engine']._authorize(
        source=expiry_setup_env['res.partner'].browse(partner_id),
        partner=expiry_setup_env['res.partner'].browse(partner_id),
        requests=[{
            'card_id': second_card_id, 'product_id': product_id,
            'uom_id': expiry_setup_env['product.product'].browse(product_id).uom_id.id,
            'quantity': 1,
        }],
        idempotency_key=f'task5:probe:authorize:expiry-other-card:{partner_id}',
    )
    expiry_other_hold_id = expiry_other.result_json['hold_id']
    expiry_setup_env['loyalty.consign.hold'].browse(
        [winner_hold_id, expiry_other_hold_id]
    )._write_from_engine({
        'expires_at': fields.Datetime.now() - timedelta(seconds=1),
    })
    expiry_setup_cr.commit()
expiry_blocker = registry.cursor()
expiry_blocker.execute(
    'SELECT id FROM loyalty_consign_hold WHERE id = %s FOR UPDATE',
    (winner_hold_id,),
)
expiry_blocker.fetchone()
with registry.cursor() as skipped_cr:
    skipped_env = api.Environment(skipped_cr, SUPERUSER_ID, {})
    assert skipped_env['loyalty.consign.hold']._cron_expire_holds(batch_size=1) == 1
    assert skipped_env['loyalty.consign.hold'].browse(winner_hold_id).state == 'active'
    assert skipped_env['loyalty.consign.hold'].browse(expiry_other_hold_id).state == 'expired'
    skipped_cr.commit()
expiry_blocker.rollback()
expiry_blocker.close()
with registry.cursor() as expiry_cr:
    expiry_env = api.Environment(expiry_cr, SUPERUSER_ID, {})
    assert expiry_env['loyalty.consign.hold']._cron_expire_holds(batch_size=1) == 1
    assert expiry_env['loyalty.consign.hold']._cron_expire_holds(batch_size=1) == 0
    expiry_cr.commit()

# A multi-card Hold is skipped as one unit when one of its card/projection
# dimensions is locked. An unrelated candidate still expires, and after the
# blocker releases the unselected multi-card dimensions are not retained by the
# still-open cron transaction.
with registry.cursor() as multi_setup_cr:
    multi_env = api.Environment(multi_setup_cr, SUPERUSER_ID, {})
    multi_program_one = multi_env['loyalty.program'].create({
        'name': 'Task5 Probe Multi Program One', 'program_type': 'consign',
        'active': True, 'company_id': company_id,
        'currency_id': multi_env.company.currency_id.id,
    })
    multi_program_two = multi_env['loyalty.program'].create({
        'name': 'Task5 Probe Multi Program Two', 'program_type': 'consign',
        'active': True, 'company_id': company_id,
        'currency_id': multi_env.company.currency_id.id,
    })
    independent_program = multi_env['loyalty.program'].create({
        'name': 'Task5 Probe Independent Program', 'program_type': 'consign',
        'active': True, 'company_id': company_id,
        'currency_id': multi_env.company.currency_id.id,
    })
    multi_one_issue = multi_env['loyalty.consign.engine']._issue(
        source=multi_env['res.partner'].browse(partner_id),
        partner=multi_env['res.partner'].browse(partner_id), program=multi_program_one,
        grants=[{'product': multi_env['product.product'].browse(product_id), 'quantity': 1}],
        idempotency_key=f'task5:probe:multi:issue-one:{partner_id}',
    )
    multi_two_issue = multi_env['loyalty.consign.engine']._issue(
        source=multi_env['res.partner'].browse(partner_id),
        partner=multi_env['res.partner'].browse(partner_id), program=multi_program_two,
        grants=[{'product': multi_env['product.product'].browse(product_id), 'quantity': 1}],
        idempotency_key=f'task5:probe:multi:issue-two:{partner_id}',
    )
    independent_issue = multi_env['loyalty.consign.engine']._issue(
        source=multi_env['res.partner'].browse(partner_id),
        partner=multi_env['res.partner'].browse(partner_id), program=independent_program,
        grants=[{'product': multi_env['product.product'].browse(product_id), 'quantity': 1}],
        idempotency_key=f'task5:probe:independent:issue:{partner_id}',
    )
    multi_operation = multi_env['loyalty.consign.engine']._authorize(
        source=multi_env['res.partner'].browse(partner_id),
        partner=multi_env['res.partner'].browse(partner_id),
        requests=[
            {'card_id': multi_one_issue.result_json['card_id'], 'product_id': product_id,
             'uom_id': multi_env['product.product'].browse(product_id).uom_id.id,
             'quantity': 1},
            {'card_id': multi_two_issue.result_json['card_id'], 'product_id': product_id,
             'uom_id': multi_env['product.product'].browse(product_id).uom_id.id,
             'quantity': 1},
        ],
        idempotency_key=f'task5:probe:multi:hold:{partner_id}',
    )
    independent_operation = multi_env['loyalty.consign.engine']._authorize(
        source=multi_env['res.partner'].browse(partner_id),
        partner=multi_env['res.partner'].browse(partner_id),
        requests=[{
            'card_id': independent_issue.result_json['card_id'], 'product_id': product_id,
            'uom_id': multi_env['product.product'].browse(product_id).uom_id.id,
            'quantity': 1,
        }],
        idempotency_key=f'task5:probe:independent:hold:{partner_id}',
    )
    multi_hold_id = multi_operation.result_json['hold_id']
    independent_hold_id = independent_operation.result_json['hold_id']
    multi_card_ids = [
        multi_one_issue.result_json['card_id'],
        multi_two_issue.result_json['card_id'],
    ]
    multi_line_ids = [
        multi_one_issue.result_json['projection_ids'][0],
        multi_two_issue.result_json['projection_ids'][0],
    ]
    multi_env['loyalty.consign.hold'].browse([
        multi_hold_id, independent_hold_id,
    ])._write_from_engine({
        'expires_at': fields.Datetime.now() - timedelta(seconds=1),
    })
    multi_setup_cr.commit()

# Block the final Hold lock. The cron probe has therefore acquired every
# multi-card card/projection dimension before the rejected candidate rolls
# its savepoint back. Keep both outer transactions open while a third cursor
# proves those earlier dimensions are not retained by the cron worker.
multi_blocker = registry.cursor()
multi_blocker.execute(
    'SELECT id FROM loyalty_consign_hold WHERE id = %s FOR UPDATE',
    (multi_hold_id,),
)
multi_blocker.fetchone()
with registry.cursor() as multi_worker_cr:
    multi_worker_env = api.Environment(multi_worker_cr, SUPERUSER_ID, {})
    assert multi_worker_env['loyalty.consign.hold']._cron_expire_holds(batch_size=1) == 1
    assert multi_worker_env['loyalty.consign.hold'].browse(multi_hold_id).state == 'active'
    assert multi_worker_env['loyalty.consign.hold'].browse(independent_hold_id).state == 'expired'
    with registry.cursor() as released_check_cr:
        for multi_card_id in multi_card_ids:
            released_check_cr.execute(
                'SELECT id FROM loyalty_card WHERE id = %s FOR UPDATE SKIP LOCKED',
                (multi_card_id,),
            )
            assert released_check_cr.fetchone()
        for multi_line_id in multi_line_ids:
            released_check_cr.execute(
                'SELECT id FROM loyalty_consign_line WHERE id = %s FOR UPDATE SKIP LOCKED',
                (multi_line_id,),
            )
            assert released_check_cr.fetchone()
        released_check_cr.rollback()
    multi_blocker.rollback()
    multi_blocker.close()
    multi_worker_cr.commit()

# Authorize versus issue reversal: the stale authorization loses; after outer
# retry, exact issue capacity is re-evaluated as a domain ValidationError.
with registry.cursor() as expire_cr:
    expire_env = api.Environment(expire_cr, SUPERUSER_ID, {})
    expire_env['loyalty.consign.hold'].browse(same_hold_id)._write_from_engine({
        'state': 'expired',
        'expired_at': fields.Datetime.now(),
        'transition_user_id': SUPERUSER_ID,
    })
    expire_env['loyalty.consign.line'].browse(line_id)._reconcile_projection()
    expire_cr.commit()

race_stale = registry.cursor()
race_stale.execute('SELECT count(*) FROM res_partner')
race_stale.fetchone()
with registry.cursor() as reversal_cr:
    reversal_env = api.Environment(reversal_cr, SUPERUSER_ID, {})
    issue_record = reversal_env['loyalty.consign.movement'].browse(issue_id)
    reversal_env['loyalty.consign.movement']._append_movement(
        aggregate_line=reversal_env['loyalty.consign.line'].browse(line_id),
        movement_type='issue_reversal', quantity=10,
        source_channel='manual', source_model='loyalty.consign.line',
        source_res_id=line_id, source_name='Task5 race reversal',
        idempotency_key=f'task5:probe:reverse:{issue_id}',
        original_movement=issue_record,
    )
    reversal_cr.commit()
try:
    authorize(
        api.Environment(race_stale, SUPERUSER_ID, {}),
        f'task5:probe:authorize:race:{partner_id}', quantity=1,
    )
except SerializationFailure:
    race_stale.rollback()
else:
    raise AssertionError('Authorize/reversal stale race was not fenced.')
finally:
    race_stale.close()
with registry.cursor() as race_retry_cr:
    race_retry_env = api.Environment(race_retry_cr, SUPERUSER_ID, {})
    try:
        authorize(
            race_retry_env, f'task5:probe:authorize:race:{partner_id}', quantity=1,
        )
    except ValidationError:
        race_retry_cr.rollback()
    else:
        raise AssertionError('Authorize/reversal retry did not fail safely.')

print('TASK5_DISTINCT_KEYS_STALE_FENCE=PASS')
print('TASK5_FRESH_RETRY_DOMAIN_ERROR=PASS')
print('TASK5_ONE_HOLD_AVAILABLE_FOUR=PASS')
print('TASK5_SAME_KEY_REPLAY=PASS')
print('TASK5_AUTHORIZE_REVERSAL_RACE=PASS')
print('TASK5_EXPIRY_BATCH_ONE_SKIPS_LOCKED=PASS')
print('TASK5_EXPIRY_MULTI_CARD_DIMENSION_SKIP=PASS')
print('TASK5_EXPIRY_UNSELECTED_DIMENSIONS_RELEASED=PASS')
print('TASK5_EXPIRY_SKIP_LOCKED_IDEMPOTENT=PASS')
print('TASK5_EXPIRY_LOCKED_FIRST_PROGRESS=PASS')
print('TASK5_NO_UNIQUE_OR_RAW_ERROR=PASS')
print('TASK5_CONCURRENCY_PROBE=PASS')
