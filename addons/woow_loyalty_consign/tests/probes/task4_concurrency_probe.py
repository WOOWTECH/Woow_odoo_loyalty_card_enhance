#!/usr/bin/env python3
"""Odoo-shell Task 4 concurrency/repair probe.

Run only against an ephemeral K3s test database, for example:
  odoo shell -d woow_task4_test < task4_concurrency_probe.py
The guard refuses production-like database names unless the operator explicitly
sets WOOW_TASK4_PROBE_ALLOW=1 in the shell environment.
"""

import os

from psycopg2.errors import SerializationFailure

from odoo import SUPERUSER_ID, api
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry


db_name = env.cr.dbname
if 'test' not in db_name.lower() and os.environ.get('WOOW_TASK4_PROBE_ALLOW') != '1':
    raise RuntimeError('Task 4 probe refuses a non-test database.')

registry = Registry(db_name)
company_id = env.company.id
product = env['product.product'].create({
    'name': 'Task4 Concurrent Entitlement', 'type': 'service', 'list_price': 80,
})
program = env['loyalty.program'].create({
    'name': 'Task4 Concurrent Program', 'program_type': 'consign', 'active': True,
    'company_id': company_id, 'currency_id': env.company.currency_id.id,
})
same_partner = env['res.partner'].create({
    'name': 'Task4 Same Key Customer', 'company_id': company_id,
})
distinct_partner = env['res.partner'].create({
    'name': 'Task4 Distinct Key Customer', 'company_id': company_id,
})
product_id, program_id = product.id, program.id
same_partner_id, distinct_partner_id = same_partner.id, distinct_partner.id
same_key = f'task4:probe:same-key:v1:{same_partner_id}'
first_key = f'task4:probe:distinct:first:v1:{distinct_partner_id}'
second_key = f'task4:probe:distinct:second:v1:{distinct_partner_id}'
env.cr.commit()


def issue(call_env, partner_id, key, quantity=2):
    partner = call_env['res.partner'].browse(partner_id)
    product_record = call_env['product.product'].browse(product_id)
    program_record = call_env['loyalty.program'].browse(program_id)
    return call_env['loyalty.consign.engine']._issue(
        source=partner,
        partner=partner,
        program=program_record,
        grants=[{
            'product': product_record,
            'uom': product_record.uom_id,
            'quantity': quantity,
            'source_line': partner,
            'source_channel': 'manual',
            'provenance_key': 'task4-concurrency-probe',
        }],
        idempotency_key=key,
    )


# Same-key stale snapshot: durable per-key token forces outer retry/replay.
stale = registry.cursor()
stale.execute('SELECT count(*) FROM res_partner')
stale.fetchone()
with registry.cursor() as winner_cr:
    winner_env = api.Environment(winner_cr, SUPERUSER_ID, {})
    winner = issue(winner_env, same_partner_id, same_key)
    winner_id = winner.id
    winner_movement_ids = winner.movement_ids.ids
    winner_cr.commit()
try:
    issue(api.Environment(stale, SUPERUSER_ID, {}), same_partner_id, same_key)
except SerializationFailure:
    stale.rollback()
    same_stale_fenced = True
else:
    stale.rollback()
    same_stale_fenced = False
finally:
    stale.close()
assert same_stale_fenced
with registry.cursor() as retry_cr:
    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
    replay = issue(retry_env, same_partner_id, same_key)
    assert replay.id == winner_id and replay.state == 'done'
    assert replay.movement_ids.ids == winner_movement_ids
    assert retry_env['loyalty.consign.operation'].search_count([
        ('idempotency_key', '=', same_key),
    ]) == 1
    assert retry_env['loyalty.consign.operation.token'].sudo().search_count([
        ('company_id', '=', company_id), ('idempotency_key', '=', same_key),
    ]) == 1
    retry_cr.commit()

# Distinct operation keys do not share the operation token. They do share the
# absent-card durable tuple, so the stale loser retries and then reuses the card.
stale_distinct = registry.cursor()
stale_distinct.execute('SELECT count(*) FROM res_partner')
stale_distinct.fetchone()
with registry.cursor() as first_cr:
    first_env = api.Environment(first_cr, SUPERUSER_ID, {})
    first = issue(first_env, distinct_partner_id, first_key, 3)
    first_card_id = first.result_json['card_id']
    first_cr.commit()
try:
    issue(
        api.Environment(stale_distinct, SUPERUSER_ID, {}),
        distinct_partner_id,
        second_key,
        4,
    )
except SerializationFailure:
    stale_distinct.rollback()
    distinct_stale_fenced = True
else:
    stale_distinct.rollback()
    distinct_stale_fenced = False
finally:
    stale_distinct.close()
assert distinct_stale_fenced
with registry.cursor() as second_cr:
    second_env = api.Environment(second_cr, SUPERUSER_ID, {})
    second = issue(second_env, distinct_partner_id, second_key, 4)
    assert second.result_json['card_id'] == first_card_id
    cards = second_env['loyalty.card'].search([
        ('program_id', '=', program_id),
        ('partner_id', '=', distinct_partner_id),
        ('active', '=', True),
    ]).filtered('is_consign')
    assert cards.ids == [first_card_id], cards.ids
    projection = cards.consign_line_ids
    assert len(projection) == 1
    assert projection.qty_issued == 7 and projection.qty_available == 7
    assert len(projection.movement_ids.filtered(
        lambda movement: movement.movement_type == 'issue'
    )) == 2
    assert projection._assert_projection_consistent()
    assert second_env['loyalty.consign.operation.token'].sudo().search_count([
        ('company_id', '=', company_id),
        ('idempotency_key', 'in', [first_key, second_key]),
    ]) == 2
    assert second_env['loyalty.consign.card.token'].sudo().search_count([
        ('company_id', '=', company_id),
        ('program_id', '=', program_id),
        ('partner_id', '=', distinct_partner_id),
    ]) == 1
    second_cr.commit()

# Repair may rewrite projection columns only and must preserve the ledger.
with registry.cursor() as repair_cr:
    repair_env = api.Environment(repair_cr, SUPERUSER_ID, {})
    projection = repair_env['loyalty.card'].browse(first_card_id).consign_line_ids
    movement_ids = projection.movement_ids.ids
    repair_cr.execute(
        'UPDATE loyalty_consign_line SET qty_available = 999 WHERE id = %s',
        (projection.id,),
    )
    projection.invalidate_recordset()
    try:
        projection._assert_projection_consistent()
    except ValidationError:
        pass
    else:
        raise AssertionError('Projection tamper was not detected.')
    assert projection.action_repair_projection()
    projection.invalidate_recordset()
    assert projection.qty_available == 7
    assert projection._assert_projection_consistent()
    assert projection.movement_ids.ids == movement_ids
    repair_cr.commit()

print('TASK4_STALE_SAME_KEY=PASS')
print('TASK4_SAME_KEY_REPLAY=PASS')
print('TASK4_ABSENT_CARD_DISTINCT_KEYS=PASS')
print('TASK4_PER_KEY_OPERATION_TOKENS=PASS')
print('TASK4_CARD_TOKEN_AND_PROJECTION=PASS')
print('TASK4_PROJECTION_REPAIR=PASS')
print('TASK4_CONCURRENCY_PROBE=PASS')
