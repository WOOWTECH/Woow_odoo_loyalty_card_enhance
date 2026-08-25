#!/usr/bin/env python3
"""Odoo-shell Task 6 real two-cursor lifecycle probe.

Run against an ephemeral test database only:
  odoo shell -d woow_task6_test < task6_lifecycle_concurrency_probe.py
Set WOOW_TASK6_PROBE_ALLOW=1 only for another disposable database name.
"""

import os
from datetime import timedelta

from psycopg2.errors import SerializationFailure

from odoo import SUPERUSER_ID, api, fields
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry


db_name = env.cr.dbname
if 'test' not in db_name.lower() and os.environ.get('WOOW_TASK6_PROBE_ALLOW') != '1':
    raise RuntimeError('Task 6 probe refuses a non-test database.')

registry = Registry(db_name)
company_id = env.company.id
partner = env['res.partner'].create({
    'name': 'Task6 Concurrent Owner', 'company_id': company_id,
})
product = env['product.product'].create({
    'name': 'Task6 Concurrent Product', 'type': 'service', 'list_price': 91,
})
program = env['loyalty.program'].create({
    'name': 'Task6 Concurrent Program', 'program_type': 'consign', 'active': True,
    'company_id': company_id, 'currency_id': env.company.currency_id.id,
})
race_product = env['product.product'].create({
    'name': 'Task6 Clawback Race Product', 'type': 'service', 'list_price': 92,
})
race_program = env['loyalty.program'].create({
    'name': 'Task6 Clawback Race Program', 'program_type': 'consign', 'active': True,
    'company_id': company_id, 'currency_id': env.company.currency_id.id,
})
partner_id = partner.id
product_id = product.id
program_id = program.id
race_product_id = race_product.id
race_program_id = race_program.id
env.cr.commit()


def issue_and_hold(call_env, key, quantity=4, issue_quantity=10):
    call_partner = call_env['res.partner'].browse(partner_id)
    call_product = call_env['product.product'].browse(product_id)
    issue = call_env['loyalty.consign.engine']._issue(
        source=call_partner,
        partner=call_partner,
        program=call_env['loyalty.program'].browse(program_id),
        grants=[{'product': call_product, 'quantity': issue_quantity}],
        idempotency_key=f'{key}:issue',
    )
    card = issue.movement_ids.card_id
    authorization = call_env['loyalty.consign.engine']._authorize(
        source=call_partner,
        partner=call_partner,
        requests=[{
            'card_id': card.id,
            'product_id': product_id,
            'uom_id': call_product.uom_id.id,
            'quantity': quantity,
        }],
        idempotency_key=f'{key}:authorize',
    )
    return issue.movement_ids.id, authorization.hold_ids.id


def lifecycle(call_env, name, hold_id, key):
    return getattr(call_env['loyalty.consign.engine'], f'_{name}')(
        source=call_env['res.partner'].browse(partner_id),
        partner=call_env['res.partner'].browse(partner_id),
        hold=call_env['loyalty.consign.hold'].browse(hold_id),
        idempotency_key=key,
    )


def expire(call_env, hold_id):
    hold = call_env['loyalty.consign.hold'].browse(hold_id)
    now = fields.Datetime.now()
    hold._write_from_engine({'expires_at': now - timedelta(seconds=1)})
    assert call_env['loyalty.consign.hold']._cron_expire_holds(
        batch_size=1, now=now,
    ) == 1


# Capture versus expiry: the stale capture loses to the expiry transition. A
# fresh retry may only return a completed capture or the controlled lifecycle
# state error; it cannot create a second redeem fact.
with registry.cursor() as setup_cr:
    setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
    _capture_issue_id, capture_hold_id = issue_and_hold(setup_env, 'task6:probe:capture')
    setup_cr.commit()
capture_stale = registry.cursor()
capture_stale.execute('SELECT count(*) FROM res_partner')
capture_stale.fetchone()
with registry.cursor() as expiry_cr:
    expiry_env = api.Environment(expiry_cr, SUPERUSER_ID, {})
    expire(expiry_env, capture_hold_id)
    expiry_cr.commit()
try:
    lifecycle(
        api.Environment(capture_stale, SUPERUSER_ID, {}), 'capture', capture_hold_id,
        'task6:probe:capture:command',
    )
except SerializationFailure:
    capture_stale.rollback()
else:
    raise AssertionError('Capture/expiry stale transaction was not fenced.')
finally:
    capture_stale.close()
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    try:
        lifecycle(retry_env, 'capture', capture_hold_id, 'task6:probe:capture:command')
    except ValidationError:
        retry_cr.rollback()
    else:
        raise AssertionError('Expired Hold capture retry did not return controlled state error.')
with registry.cursor() as check_cr:
    check_env = api.Environment(check_cr, SUPERUSER_ID, {})
    captured_hold = check_env['loyalty.consign.hold'].browse(capture_hold_id)
    assert captured_hold.state == 'expired'
    assert check_env['loyalty.consign.movement'].search_count([
        ('operation_id.idempotency_key', '=', 'task6:probe:capture:command'),
    ]) == 0
    check_cr.commit()

# Release versus expiry has identical stale fencing and cannot double-transition
# the Hold. Its release command must not append movements.
with registry.cursor() as setup_cr:
    setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
    _release_issue_id, release_hold_id = issue_and_hold(setup_env, 'task6:probe:release')
    release_hold = setup_env['loyalty.consign.hold'].browse(release_hold_id)
    release_line = release_hold.allocation_line_ids.aggregate_line_id
    release_line_id = release_line.id
    release_expected_available = (
        release_line.qty_available
        + sum(release_hold.allocation_line_ids.mapped('quantity'))
    )
    setup_cr.commit()
release_stale = registry.cursor()
release_stale.execute('SELECT count(*) FROM res_partner')
release_stale.fetchone()
with registry.cursor() as expiry_cr:
    expiry_env = api.Environment(expiry_cr, SUPERUSER_ID, {})
    expire(expiry_env, release_hold_id)
    expiry_cr.commit()
try:
    lifecycle(
        api.Environment(release_stale, SUPERUSER_ID, {}), 'release', release_hold_id,
        'task6:probe:release:command',
    )
except SerializationFailure:
    release_stale.rollback()
else:
    raise AssertionError('Release/expiry stale transaction was not fenced.')
finally:
    release_stale.close()
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    try:
        lifecycle(retry_env, 'release', release_hold_id, 'task6:probe:release:command')
    except ValidationError:
        retry_cr.rollback()
    else:
        raise AssertionError('Expired Hold release retry did not return controlled state error.')
with registry.cursor() as check_cr:
    check_env = api.Environment(check_cr, SUPERUSER_ID, {})
    released_hold = check_env['loyalty.consign.hold'].browse(release_hold_id)
    line = check_env['loyalty.consign.line'].browse(release_line_id)
    assert released_hold.state == 'expired'
    assert line.qty_on_hold == 0
    assert line.qty_available == release_expected_available
    assert check_env['loyalty.consign.movement'].search_count([
        ('operation_id.idempotency_key', '=', 'task6:probe:release:command'),
    ]) == 0
    check_cr.commit()

# Same-key concurrent capture: stale same-key command fences on its durable
# token, and a fresh retry returns the one completed capture and exact redeems.
with registry.cursor() as setup_cr:
    setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
    _same_issue_id, same_hold_id = issue_and_hold(setup_env, 'task6:probe:same', quantity=4)
    setup_cr.commit()
same_stale = registry.cursor()
same_stale.execute('SELECT count(*) FROM res_partner')
same_stale.fetchone()
same_key = 'task6:probe:same:capture'
with registry.cursor() as winner_cr:
    winner_env = api.Environment(winner_cr, SUPERUSER_ID, {})
    winner = lifecycle(winner_env, 'capture', same_hold_id, same_key)
    winner_id = winner.id
    winner_movement_ids = sorted(winner.movement_ids.ids)
    winner_cr.commit()
try:
    lifecycle(api.Environment(same_stale, SUPERUSER_ID, {}), 'capture', same_hold_id, same_key)
except SerializationFailure:
    same_stale.rollback()
else:
    raise AssertionError('Same-key capture stale transaction was not fenced.')
finally:
    same_stale.close()
with registry.cursor() as replay_cr:
    replay_env = api.Environment(replay_cr, SUPERUSER_ID, {})
    replay = lifecycle(replay_env, 'capture', same_hold_id, same_key)
    assert replay.id == winner_id
    assert sorted(replay.movement_ids.ids) == winner_movement_ids
    assert len(replay.movement_ids) == 1
    assert replay_env['loyalty.consign.hold'].browse(same_hold_id).state == 'captured'
    replay_cr.commit()

# Issue clawback versus authorization: a stale clawback must lose, and a fresh
# retry cannot revoke capacity now held by the winner authorization.
with registry.cursor() as setup_cr:
    setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
    setup_partner = setup_env['res.partner'].browse(partner_id)
    setup_issue = setup_env['loyalty.consign.engine']._issue(
        source=setup_partner,
        partner=setup_partner,
        program=setup_env['loyalty.program'].browse(race_program_id),
        grants=[{
            'product': setup_env['product.product'].browse(race_product_id), 'quantity': 10,
        }],
        idempotency_key='task6:probe:clawback:issue',
    )
    issue = setup_issue.movement_ids
    race_issue_id = issue.id
    card_id = issue.card_id.id
    line_id = issue.aggregate_line_id.id
    setup_cr.commit()
clawback_stale = registry.cursor()
clawback_stale.execute('SELECT count(*) FROM res_partner')
clawback_stale.fetchone()
with registry.cursor() as winner_cr:
    winner_env = api.Environment(winner_cr, SUPERUSER_ID, {})
    winner_authorization = winner_env['loyalty.consign.engine']._authorize(
        source=winner_env['res.partner'].browse(partner_id),
        partner=winner_env['res.partner'].browse(partner_id),
        requests=[{
            'card_id': card_id,
            'product_id': race_product_id,
            'uom_id': winner_env['product.product'].browse(race_product_id).uom_id.id,
            'quantity': 6,
        }],
        idempotency_key='task6:probe:clawback:authorize',
    )
    race_authorize_hold_id = winner_authorization.hold_ids.id
    winner_cr.commit()
try:
    api.Environment(clawback_stale, SUPERUSER_ID, {})['loyalty.consign.engine']._clawback_issue(
        source=api.Environment(clawback_stale, SUPERUSER_ID, {})['res.partner'].browse(partner_id),
        partner=api.Environment(clawback_stale, SUPERUSER_ID, {})['res.partner'].browse(partner_id),
        issue_movement=api.Environment(clawback_stale, SUPERUSER_ID, {})['loyalty.consign.movement'].browse(race_issue_id),
        quantity=10,
        idempotency_key='task6:probe:clawback:command',
    )
except SerializationFailure:
    clawback_stale.rollback()
else:
    raise AssertionError('Clawback/authorization stale transaction was not fenced.')
finally:
    clawback_stale.close()
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    try:
        retry_env['loyalty.consign.engine']._clawback_issue(
            source=retry_env['res.partner'].browse(partner_id),
            partner=retry_env['res.partner'].browse(partner_id),
            issue_movement=retry_env['loyalty.consign.movement'].browse(race_issue_id),
            quantity=10,
            idempotency_key='task6:probe:clawback:command',
        )
    except ValidationError:
        retry_cr.rollback()
    else:
        raise AssertionError('Fresh clawback retry did not reject held capacity.')
with registry.cursor() as check_cr:
    check_env = api.Environment(check_cr, SUPERUSER_ID, {})
    line = check_env['loyalty.consign.line'].browse(line_id)
    assert line.qty_on_hold == 6 and line.qty_available == 4
    assert check_env['loyalty.consign.movement'].search_count([
        ('operation_id.idempotency_key', '=', 'task6:probe:clawback:command'),
    ]) == 0
    check_cr.commit()

# Capture versus clawback uses the exact linked issue.  Capture locks that
# issue operation before its projection; the stale clawback therefore fences
# on the same durable token instead of forming an inverse lock cycle.  Its
# fresh retry can claw back only the exact post-capture remainder.
with registry.cursor() as release_cr:
    release_env = api.Environment(release_cr, SUPERUSER_ID, {})
    lifecycle(
        release_env, 'release', race_authorize_hold_id,
        'task6:probe:capture-clawback:release-prior-hold',
    )
    release_cr.commit()
with registry.cursor() as setup_cr:
    setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
    capture_authorization = setup_env['loyalty.consign.engine']._authorize(
        source=setup_env['res.partner'].browse(partner_id),
        partner=setup_env['res.partner'].browse(partner_id),
        requests=[{
            'card_id': card_id,
            'product_id': race_product_id,
            'uom_id': setup_env['product.product'].browse(race_product_id).uom_id.id,
            'quantity': 6,
        }],
        idempotency_key='task6:probe:capture-clawback:authorize',
    )
    capture_clawback_hold_id = capture_authorization.hold_ids.id
    setup_cr.commit()
clawback_stale = registry.cursor()
clawback_stale.execute('SELECT count(*) FROM res_partner')
clawback_stale.fetchone()
with registry.cursor() as winner_cr:
    winner_env = api.Environment(winner_cr, SUPERUSER_ID, {})
    capture = lifecycle(
        winner_env, 'capture', capture_clawback_hold_id,
        'task6:probe:capture-clawback:capture',
    )
    assert len(capture.movement_ids) == 1
    assert capture.movement_ids.quantity == 6
    winner_cr.commit()
try:
    stale_env = api.Environment(clawback_stale, SUPERUSER_ID, {})
    stale_env['loyalty.consign.engine']._clawback_issue(
        source=stale_env['res.partner'].browse(partner_id),
        partner=stale_env['res.partner'].browse(partner_id),
        issue_movement=stale_env['loyalty.consign.movement'].browse(race_issue_id),
        quantity=4,
        idempotency_key='task6:probe:capture-clawback:clawback',
    )
except SerializationFailure:
    clawback_stale.rollback()
else:
    raise AssertionError('Capture/clawback stale transaction was not fenced.')
finally:
    clawback_stale.close()
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    clawback = retry_env['loyalty.consign.engine']._clawback_issue(
        source=retry_env['res.partner'].browse(partner_id),
        partner=retry_env['res.partner'].browse(partner_id),
        issue_movement=retry_env['loyalty.consign.movement'].browse(race_issue_id),
        quantity=4,
        idempotency_key='task6:probe:capture-clawback:clawback',
    )
    assert clawback.movement_ids.movement_type == 'issue_reversal'
    assert clawback.movement_ids.quantity == 4
    retry_cr.commit()
with registry.cursor() as check_cr:
    check_env = api.Environment(check_cr, SUPERUSER_ID, {})
    line = check_env['loyalty.consign.line'].browse(line_id)
    issue = check_env['loyalty.consign.movement'].browse(race_issue_id)
    states = check_env['loyalty.consign.movement']._fifo_issue_availability(
        line, include_active_holds=False,
    )
    issue_capacity = next(
        state['available'] for state in states if state['issue'] == issue
    )
    assert check_env['loyalty.consign.hold'].browse(capture_clawback_hold_id).state == 'captured'
    assert line.qty_on_hold == 0 and line.qty_available == 0
    assert issue_capacity == 0
    assert check_env['loyalty.consign.movement'].search_count([
        ('operation_id.idempotency_key', '=', 'task6:probe:capture-clawback:capture'),
    ]) == 1
    assert check_env['loyalty.consign.movement'].search_count([
        ('operation_id.idempotency_key', '=', 'task6:probe:capture-clawback:clawback'),
    ]) == 1
    assert check_env['loyalty.consign.movement'].search_count([
        ('original_movement_id', '=', race_issue_id),
    ]) == 2
    check_cr.commit()

print('TASK6_CAPTURE_EXPIRY_STALE_FENCE=PASS')
print('TASK6_CAPTURE_EXPIRY_CONTROLLED_RETRY=PASS')
print('TASK6_CAPTURE_NO_DOUBLE_REDEEM=PASS')
print('TASK6_RELEASE_EXPIRY_STALE_FENCE=PASS')
print('TASK6_RELEASE_EXPIRY_CONTROLLED_RETRY=PASS')
print('TASK6_RELEASE_NO_DOUBLE_TRANSITION=PASS')
print('TASK6_RELEASE_PROJECTION_RECONCILED=PASS')
print('TASK6_SAME_KEY_CAPTURE_REPLAY=PASS')
print('TASK6_SAME_KEY_EXACT_REDEEMS=PASS')
print('TASK6_CLAWBACK_AUTHORIZE_STALE_FENCE=PASS')
print('TASK6_CLAWBACK_AUTHORIZE_CONTROLLED_RETRY=PASS')
print('TASK6_CLAWBACK_AUTHORIZE_NO_OVERCONSUME=PASS')
print('TASK6_CAPTURE_CLAWBACK_STALE_FENCE=PASS')
print('TASK6_CAPTURE_CLAWBACK_SAFE_RETRY=PASS')
print('TASK6_CAPTURE_CLAWBACK_LOCK_ORDER=PASS')
print('TASK6_CAPTURE_CLAWBACK_NO_OVERCONSUME=PASS')
print('TASK6_NO_UNIQUE_OR_RAW_ERROR=PASS')
print('TASK6_CONCURRENCY_PROBE=PASS')
