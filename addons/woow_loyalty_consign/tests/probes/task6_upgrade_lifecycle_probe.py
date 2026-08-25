#!/usr/bin/env python3
"""Task 5 -> Task 6 lifecycle upgrade fixture and verification probe.

Run only on a disposable test database.  Prepare with Task 5 addons first:
  WOOW_TASK6_UPGRADE_PHASE=prepare odoo shell -d woow_task6_upgrade_test \
    < task6_upgrade_lifecycle_probe.py
Upgrade ``woow_loyalty_consign`` to Task 6, then verify with Task 6 addons:
  odoo shell -d woow_task6_upgrade_test < task6_upgrade_lifecycle_probe.py

The fixture intentionally creates Task 5 active Holds only.  The Task 6 verify
phase proves they upgrade into a lifecycle containing active, captured,
released, and expired Holds without rewriting historical ledger facts.
"""

import os
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError


if 'test' not in env.cr.dbname.lower() and os.environ.get('WOOW_TASK6_PROBE_ALLOW') != '1':
    raise RuntimeError('Task 6 upgrade probe refuses a non-test database.')

params = env['ir.config_parameter'].sudo()
phase = os.environ.get('WOOW_TASK6_UPGRADE_PHASE', 'verify')
prefix = 'woow.task6.upgrade.'


def set_id(name, record):
    params.set_param(f'{prefix}{name}', record.id)


def get_id(name):
    value = params.get_param(f'{prefix}{name}')
    if not value:
        raise AssertionError(f'Missing Task 5 upgrade fixture value: {name}')
    return int(value)


if phase == 'prepare':
    partner = env['res.partner'].create({
        'name': 'Task6 Upgrade Owner', 'company_id': env.company.id,
    })
    product = env['product.product'].create({
        'name': 'Task6 Upgrade Product', 'type': 'service', 'list_price': 124,
    })
    program = env['loyalty.program'].create({
        'name': 'Task6 Upgrade Program', 'program_type': 'consign', 'active': True,
        'company_id': env.company.id, 'currency_id': env.company.currency_id.id,
    })
    issue = env['loyalty.consign.engine']._issue(
        source=partner, partner=partner, program=program,
        grants=[{'product': product, 'quantity': 12}],
        idempotency_key=f'task6:upgrade:issue:{partner.id}',
    )
    card = issue.movement_ids.card_id
    holds = []
    for purpose in ('active', 'capture', 'release', 'expire'):
        authorization = env['loyalty.consign.engine']._authorize(
            source=partner, partner=partner,
            requests=[{
                'card_id': card.id, 'product_id': product.id,
                'uom_id': product.uom_id.id, 'quantity': 2,
            }],
            idempotency_key=f'task6:upgrade:authorize:{purpose}:{partner.id}',
        )
        holds.append(authorization.hold_ids)
    for name, record in (
        ('partner', partner), ('product', product), ('card', card),
        ('line', issue.movement_ids.aggregate_line_id), ('issue', issue.movement_ids),
        ('active_hold', holds[0]), ('capture_hold', holds[1]),
        ('release_hold', holds[2]), ('expire_hold', holds[3]),
    ):
        set_id(name, record)
    env.cr.commit()
    print('TASK6_UPGRADE_TASK5_FIXTURE=PASS')
    print('TASK6_UPGRADE_PREPARE=PASS')
elif phase == 'verify':
    partner = env['res.partner'].browse(get_id('partner'))
    product = env['product.product'].browse(get_id('product'))
    card = env['loyalty.card'].browse(get_id('card'))
    line = env['loyalty.consign.line'].browse(get_id('line'))
    issue = env['loyalty.consign.movement'].browse(get_id('issue'))
    active_hold = env['loyalty.consign.hold'].browse(get_id('active_hold'))
    capture_hold = env['loyalty.consign.hold'].browse(get_id('capture_hold'))
    release_hold = env['loyalty.consign.hold'].browse(get_id('release_hold'))
    expire_hold = env['loyalty.consign.hold'].browse(get_id('expire_hold'))
    assert all(record.exists() for record in (
        partner, product, card, line, issue, active_hold, capture_hold, release_hold, expire_hold,
    ))
    assert all(hold.state == 'active' for hold in (
        active_hold, capture_hold, release_hold, expire_hold,
    ))

    capture_key = f'task6:upgrade:capture:{capture_hold.id}'
    capture = env['loyalty.consign.engine']._capture(
        source=partner, partner=partner, hold=capture_hold, idempotency_key=capture_key,
    )
    assert capture_hold.state == 'captured'
    assert len(capture.movement_ids) == 1
    redeem = capture.movement_ids
    assert redeem.movement_type == 'redeem'
    assert redeem.original_movement_id == issue
    assert redeem.quantity == 2 and redeem.unit_value == issue.unit_value
    assert env['loyalty.consign.engine']._capture(
        source=partner, partner=partner, hold=capture_hold, idempotency_key=capture_key,
    ) == capture

    release_key = f'task6:upgrade:release:{release_hold.id}'
    release = env['loyalty.consign.engine']._release(
        source=partner, partner=partner, hold=release_hold, idempotency_key=release_key,
    )
    assert release_hold.state == 'released' and not release.movement_ids
    assert env['loyalty.consign.engine']._release(
        source=partner, partner=partner, hold=release_hold, idempotency_key=release_key,
    ) == release

    now = fields.Datetime.now()
    expire_hold._write_from_engine({'expires_at': now - timedelta(seconds=1)})
    assert env['loyalty.consign.hold']._cron_expire_holds(batch_size=1, now=now) == 1
    assert expire_hold.state == 'expired'

    line.invalidate_recordset()
    assert line.qty_issued == 12
    assert line.qty_redeemed == 2
    assert line.qty_on_hold == 2
    assert line.qty_available == 8
    with env.cr.savepoint():
        try:
            card.write({'active': False})
        except ValidationError:
            pass
        else:
            raise AssertionError('Active Hold did not block card deactivation.')
    active_release = env['loyalty.consign.engine']._release(
        source=partner, partner=partner, hold=active_hold,
        idempotency_key=f'task6:upgrade:release-active:{active_hold.id}',
    )
    assert active_release.result_json['hold_id'] == active_hold.id
    assert active_hold.state == 'released'
    card.write({'active': False})
    assert env['loyalty.consign.engine']._capture(
        source=partner, partner=partner, hold=capture_hold, idempotency_key=capture_key,
    ) == capture
    env.cr.commit()
    print('TASK6_UPGRADE_ACTIVE_CAPTURED_RELEASED_EXPIRED=PASS')
    print('TASK6_UPGRADE_LEDGER_PROJECTION=PASS')
    print('TASK6_UPGRADE_DEACTIVATION_GUARD=PASS')
    print('TASK6_UPGRADE_CAPTURE_RELEASE_REPLAY=PASS')
    print('TASK6_UPGRADE_LIFECYCLE_PROBE=PASS')
else:
    raise RuntimeError('WOOW_TASK6_UPGRADE_PHASE must be prepare or verify.')
