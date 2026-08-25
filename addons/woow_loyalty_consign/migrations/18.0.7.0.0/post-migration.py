"""Link pre-Task-7 completed redemption audits to their exact legacy capture.

Task 6 posted each legacy backend redemption movement through the operation
journal, but its document was not yet an engine adapter.  Task 7 makes the
document immutable, so this post-migration is the sole controlled repair point.
It does not create or rewrite ledger facts: a document is linked only when all
of its posted redemption movements belong to exactly one existing, completed
capture operation and that operation owns no other redemption movement.
"""

from odoo import SUPERUSER_ID, api


_LEGACY_SOURCE_MODEL = 'loyalty.consign.redemption.line'


def _legacy_capture_rows(cr):
    """Return one exact capture mapping per completed legacy document.

    A legacy `action_done` command created a capture journal for each posted
    chunk.  The Task-7 document has a single capture relation, therefore a
    multi-journal document is deliberately rejected rather than silently
    selecting one partial result.
    """
    cr.execute(
        '''
        SELECT redemption.id,
               redemption.company_id,
               redemption.partner_id,
               redemption.card_id,
               ARRAY_AGG(movement.id ORDER BY movement.id),
               MIN(operation.id) AS capture_operation_id,
               COUNT(DISTINCT operation.id) AS operation_count,
               COUNT(movement.id) AS movement_count,
               COUNT(DISTINCT operation_movement.id) AS operation_movement_count,
               BOOL_AND(operation.operation_type = 'capture'
                        AND operation.state = 'done'
                        AND operation.company_id = redemption.company_id
                        AND operation.partner_id = redemption.partner_id) AS operation_matches
          FROM loyalty_consign_redemption redemption
          JOIN loyalty_consign_redemption_line audit_line
            ON audit_line.redemption_id = redemption.id
          LEFT JOIN loyalty_consign_movement movement
            ON movement.source_model = %s
           AND movement.source_res_id = audit_line.id
           AND movement.movement_type = 'redeem'
          LEFT JOIN loyalty_consign_operation operation
            ON operation.id = movement.operation_id
          LEFT JOIN loyalty_consign_movement operation_movement
            ON operation_movement.operation_id = operation.id
           AND operation_movement.movement_type = 'redeem'
         WHERE redemption.state = 'done'
           AND redemption.capture_operation_id IS NULL
      GROUP BY redemption.id, redemption.company_id, redemption.partner_id,
               redemption.card_id
      ORDER BY redemption.id
        ''',
        (_LEGACY_SOURCE_MODEL,),
    )
    return cr.fetchall()


def migrate(cr, version):
    """Attach only exact legacy captures before audit-document guards apply."""
    # A direct install, or a database without historical redemption documents,
    # has nothing to repair.  The schema is present by the post-migration phase.
    cr.execute("SELECT to_regclass('loyalty_consign_redemption') IS NOT NULL")
    if not cr.fetchone()[0]:
        return

    # The two Task-7 fields are stored card relations.  Populate their
    # deterministic historical values before comparing operation dimensions;
    # relying on a deferred ORM recompute would make the migration order
    # dependent.
    cr.execute(
        '''
        UPDATE loyalty_consign_redemption redemption
           SET company_id = card.company_id,
               partner_id = card.partner_id
          FROM loyalty_card card
         WHERE card.id = redemption.card_id
           AND (redemption.company_id IS DISTINCT FROM card.company_id
                OR redemption.partner_id IS DISTINCT FROM card.partner_id)
        '''
    )

    rows = _legacy_capture_rows(cr)
    invalid = []
    mappings = []
    for (
        redemption_id, company_id, partner_id, card_id, movement_ids,
        capture_operation_id, operation_count, movement_count,
        operation_movement_count, operation_matches,
    ) in rows:
        # Every redemption movement must point at the one candidate capture.
        # `operation_movement_count == movement_count` prevents linking an
        # operation that also owns movements from another audit document.
        if (
            not movement_ids
            or movement_count != operation_movement_count
            or operation_count != 1
            or not operation_matches
        ):
            invalid.append(redemption_id)
            continue
        mappings.append((redemption_id, capture_operation_id))

    if invalid:
        raise RuntimeError(
            'Task 7 cannot safely link completed legacy redemption audit '
            'documents with missing or ambiguous capture facts: %s. '
            'No ledger facts or audit documents were changed.' % invalid
        )

    # This is deliberately SQL rather than ORM: Task 7's target model rejects
    # writes to completed audit documents.  We only add deterministic metadata
    # and an FK to an already-posted capture operation; movements stay untouched.
    for redemption_id, capture_operation_id in mappings:
        cr.execute(
            '''
            UPDATE loyalty_consign_redemption
               SET capture_operation_id = %s,
                   submission_uuid = COALESCE(
                       NULLIF(submission_uuid, ''),
                       %s
                   )
             WHERE id = %s
               AND state = 'done'
               AND capture_operation_id IS NULL
            ''',
            (
                capture_operation_id,
                'consign:migration:task7:redemption:%s' % redemption_id,
                redemption_id,
            ),
        )
        if cr.rowcount != 1:
            raise RuntimeError(
                'Task 7 could not atomically link completed redemption %s.'
                % redemption_id
            )

    # Verify the resulting document relation exposes exactly its pre-existing
    # movements.  An assertion failure aborts the entire module upgrade.
    env = api.Environment(cr, SUPERUSER_ID, {})
    documents = env['loyalty.consign.redemption'].sudo().search([
        ('state', '=', 'done'), ('capture_operation_id', '!=', False),
    ])
    for document in documents:
        source_movement_ids = env['loyalty.consign.movement'].sudo().search([
            ('source_model', '=', _LEGACY_SOURCE_MODEL),
            ('source_res_id', 'in', document.line_ids.ids),
            ('movement_type', '=', 'redeem'),
        ], order='id').ids
        if source_movement_ids and document.movement_ids.ids != source_movement_ids:
            raise RuntimeError(
                'Task 7 linked redemption %s to a non-exact capture movement set.'
                % document.id
            )
