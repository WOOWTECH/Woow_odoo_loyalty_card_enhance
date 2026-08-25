#!/usr/bin/env python3
"""Guarded Task-6 -> Task-7 backend-audit upgrade contract.

Run only on a disposable database.  Prepare using Task-6 addon code:
  WOOW_TASK7_UPGRADE_PHASE=prepare odoo shell -d woow_task7_upgrade_test \
    < task7_upgrade_backend_probe.py
Upgrade woow_loyalty_consign with Task-7 code, then verify:
  odoo shell -d woow_task7_upgrade_test < task7_upgrade_backend_probe.py
"""

import os

from odoo.exceptions import ValidationError


if 'test' not in env.cr.dbname.lower() and os.environ.get('WOOW_TASK7_PROBE_ALLOW') != '1':
    raise RuntimeError('Task 7 upgrade probe refuses a non-test database.')

params = env['ir.config_parameter'].sudo()
prefix = 'woow.task7.upgrade.'
phase = os.environ.get('WOOW_TASK7_UPGRADE_PHASE', 'verify')


def set_id(name, record):
    params.set_param('%s%s' % (prefix, name), record.id)


def get_id(name):
    value = params.get_param('%s%s' % (prefix, name))
    if not value:
        raise AssertionError('Missing Task 7 fixture value: %s' % name)
    return int(value)


if phase == 'prepare':
    partner = env['res.partner'].create({
        'name': 'Task7 Upgrade Owner', 'company_id': env.company.id,
    })
    product = env['product.product'].create({
        'name': 'Task7 Upgrade Product', 'type': 'service', 'list_price': 137,
    })
    program = env['loyalty.program'].create({
        'name': 'Task7 Upgrade Program', 'program_type': 'consign', 'active': True,
        'company_id': env.company.id, 'currency_id': env.company.currency_id.id,
    })
    issue = env['loyalty.consign.engine']._issue(
        source=partner, partner=partner, program=program,
        grants=[{'product': product, 'quantity': 5}],
        idempotency_key='task7:upgrade:issue:%s' % partner.id,
    )
    line = issue.movement_ids.aggregate_line_id
    document = env['loyalty.consign.redemption'].create({
        'card_id': issue.movement_ids.card_id.id,
        'service_note': 'pre-task7 completed audit',
        'line_ids': [(0, 0, {'consign_line_id': line.id, 'qty_redeemed': 2})],
    })
    document.action_done()
    movement = env['loyalty.consign.movement'].search([
        ('source_model', '=', 'loyalty.consign.redemption.line'),
        ('source_res_id', '=', document.line_ids.id),
        ('movement_type', '=', 'redeem'),
    ])
    if document.state != 'done' or len(movement) != 1:
        raise AssertionError('Task 6 fixture did not create one completed legacy capture.')
    set_id('document', document)
    set_id('movement', movement)
    set_id('capture_operation', movement.operation_id)
    env.cr.commit()
    print('TASK7_UPGRADE_TASK6_FIXTURE=PASS')
elif phase == 'verify':
    document = env['loyalty.consign.redemption'].browse(get_id('document'))
    movement = env['loyalty.consign.movement'].browse(get_id('movement'))
    capture = env['loyalty.consign.operation'].browse(get_id('capture_operation'))
    if document.state != 'done':
        raise AssertionError('Completed legacy audit state changed during upgrade.')
    if document.capture_operation_id != capture:
        raise AssertionError('Task 7 did not link the exact legacy capture operation.')
    if document.movement_ids != movement:
        raise AssertionError('Task 7 audit does not expose its exact legacy movement.')
    if not document.submission_uuid.startswith('consign:migration:task7:redemption:'):
        raise AssertionError('Task 7 migration did not assign deterministic audit UUID.')
    try:
        document.write({'service_note': 'must remain immutable'})
    except ValidationError:
        pass
    else:
        raise AssertionError('Task 7 accepted a write to a completed legacy audit.')
    if env['loyalty.consign.movement'].browse(movement.id).operation_id != capture:
        raise AssertionError('Migration rewrote the append-only movement relation.')
    print('TASK7_UPGRADE_VERIFY=PASS')
else:
    raise RuntimeError('Unknown WOOW_TASK7_UPGRADE_PHASE: %s' % phase)
