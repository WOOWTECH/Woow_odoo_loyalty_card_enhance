"""Controlled legacy -> Task 4 aggregate projection consolidation.

This pre-migration intentionally supports databases both with and without the
Task 3 ledger tables. Every mutation runs in Odoo's single module-upgrade
transaction; any failed assertion rolls the complete consolidation back.
"""

import math

from psycopg2 import sql


_TRIGGER = 'woow_loyalty_consign_movement_immutable_trg'
_FUNCTION = 'woow_loyalty_consign_movement_immutable_guard'


def _scalar(cr, statement, params=()):
    cr.execute(statement, params)
    return cr.fetchone()[0]


def _table_exists(cr, table_name):
    return bool(_scalar(cr, 'SELECT to_regclass(%s) IS NOT NULL', (table_name,)))


def _column_exists(cr, table_name, column_name):
    return bool(_scalar(
        cr,
        '''SELECT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s AND column_name = %s
           )''',
        (table_name, column_name),
    ))


def _metric(cr, table_name, expression):
    if not _table_exists(cr, table_name):
        return 0
    return _scalar(
        cr,
        sql.SQL('SELECT {} FROM {}').format(
            sql.SQL(expression), sql.Identifier(table_name),
        ),
    )


def _foreign_keys(cr):
    cr.execute(
        '''
        SELECT source.relname, source_column.attname, constraint_record.conname
          FROM pg_constraint constraint_record
          JOIN pg_class source ON source.oid = constraint_record.conrelid
          JOIN pg_namespace namespace ON namespace.oid = source.relnamespace
          JOIN pg_attribute source_column
            ON source_column.attrelid = source.oid
           AND source_column.attnum = constraint_record.conkey[1]
         WHERE constraint_record.contype = 'f'
           AND constraint_record.confrelid = 'loyalty_consign_line'::regclass
           AND cardinality(constraint_record.conkey) = 1
           AND cardinality(constraint_record.confkey) = 1
           AND namespace.nspname = current_schema()
      ORDER BY source.relname, source_column.attname, constraint_record.conname
        '''
    )
    return cr.fetchall()


def _ensure_trigger(cr):
    if not _table_exists(cr, 'loyalty_consign_movement'):
        return
    cr.execute(
        sql.SQL(
            '''
            CREATE OR REPLACE FUNCTION {}()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'loyalty.consign.movement rows are immutable';
            END;
            $$ LANGUAGE plpgsql
            '''
        ).format(sql.Identifier(_FUNCTION))
    )
    cr.execute(
        sql.SQL('DROP TRIGGER IF EXISTS {} ON loyalty_consign_movement').format(
            sql.Identifier(_TRIGGER)
        )
    )
    cr.execute(
        sql.SQL(
            '''CREATE TRIGGER {} BEFORE UPDATE OR DELETE
                 ON loyalty_consign_movement FOR EACH ROW EXECUTE FUNCTION {}()'''
        ).format(sql.Identifier(_TRIGGER), sql.Identifier(_FUNCTION))
    )


def _assert_ledger_dimensions(cr):
    assertions = []
    if _table_exists(cr, 'loyalty_consign_movement'):
        assertions.append((
            '''SELECT COUNT(*) FROM loyalty_consign_movement movement
                JOIN loyalty_consign_line line ON line.id = movement.aggregate_line_id
               WHERE (movement.card_id, movement.partner_id,
                      movement.product_id, movement.product_uom_id)
                     IS DISTINCT FROM
                     (line.card_id, line.partner_id,
                      line.product_id, line.product_uom_id)''',
            'movement',
        ))
    if _table_exists(cr, 'loyalty_consign_hold_allocation'):
        assertions.append((
            '''SELECT COUNT(*) FROM loyalty_consign_hold_allocation allocation
                JOIN loyalty_consign_line line ON line.id = allocation.aggregate_line_id
                JOIN loyalty_consign_movement movement
                  ON movement.id = allocation.issue_movement_id
               WHERE (allocation.card_id, allocation.product_id,
                      allocation.product_uom_id, allocation.aggregate_line_id)
                     IS DISTINCT FROM
                     (line.card_id, line.product_id, line.product_uom_id,
                      movement.aggregate_line_id)''',
            'Hold allocation',
        ))
    if _table_exists(cr, 'loyalty_consign_redemption_line') and all(
        _column_exists(cr, 'loyalty_consign_redemption_line', column)
        for column in ('product_id', 'product_uom_id')
    ):
        assertions.append((
            '''SELECT COUNT(*) FROM loyalty_consign_redemption_line redemption
                JOIN loyalty_consign_line line ON line.id = redemption.consign_line_id
               WHERE (redemption.product_id, redemption.product_uom_id)
                     IS DISTINCT FROM (line.product_id, line.product_uom_id)''',
            'redemption',
        ))
    for statement, label in assertions:
        if _scalar(cr, statement):
            raise RuntimeError(
                f'Consignment projection consolidation found corrupt {label} dimensions.'
            )


def _verify_unique_constraint(cr):
    validated = _scalar(
        cr,
        '''SELECT COALESCE(bool_and(convalidated), FALSE)
             FROM pg_constraint
            WHERE conrelid = 'loyalty_consign_line'::regclass
              AND conname = 'loyalty_consign_line_card_product_uom_unique' ''',
    )
    if not validated:
        raise RuntimeError('The aggregate projection unique constraint is not validated.')
    # A nested PL/pgSQL exception block is a subtransaction. The expected
    # unique violation is swallowed without poisoning the upgrade transaction.
    cr.execute(
        '''
        DO $$
        DECLARE rejected boolean := FALSE;
        DECLARE column_list text;
        BEGIN
            IF EXISTS (SELECT 1 FROM loyalty_consign_line) THEN
                SELECT string_agg(quote_ident(attribute.attname), ', '
                                  ORDER BY attribute.attnum)
                  INTO column_list
                  FROM pg_attribute attribute
                 WHERE attribute.attrelid = 'loyalty_consign_line'::regclass
                   AND attribute.attnum > 0
                   AND NOT attribute.attisdropped
                   AND attribute.attname <> 'id'
                   AND attribute.attgenerated = '';
                BEGIN
                    EXECUTE format(
                        'INSERT INTO loyalty_consign_line (%1$s) '
                        'SELECT %1$s FROM loyalty_consign_line ORDER BY id LIMIT 1',
                        column_list
                    );
                EXCEPTION WHEN unique_violation THEN
                    rejected := TRUE;
                END;
                IF NOT rejected THEN
                    RAISE EXCEPTION 'projection unique constraint accepted a duplicate';
                END IF;
            END IF;
        END $$
        '''
    )


def _verify_trigger_rejects_mutation(cr):
    if (
        not _table_exists(cr, 'loyalty_consign_movement')
        or not _scalar(cr, 'SELECT EXISTS (SELECT 1 FROM loyalty_consign_movement)')
    ):
        return
    cr.execute(
        '''
        DO $$
        DECLARE update_blocked boolean := FALSE;
        DECLARE delete_blocked boolean := FALSE;
        DECLARE error_text text;
        BEGIN
            BEGIN
                UPDATE loyalty_consign_movement SET quantity = quantity
                 WHERE id = (SELECT MIN(id) FROM loyalty_consign_movement);
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS error_text = MESSAGE_TEXT;
                IF position('immutable' in error_text) = 0 THEN RAISE; END IF;
                update_blocked := TRUE;
            END;
            IF NOT update_blocked THEN
                RAISE EXCEPTION 'movement trigger accepted an update';
            END IF;
            BEGIN
                DELETE FROM loyalty_consign_movement
                 WHERE id = (SELECT MIN(id) FROM loyalty_consign_movement);
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS error_text = MESSAGE_TEXT;
                IF position('immutable' in error_text) = 0 THEN RAISE; END IF;
                delete_blocked := TRUE;
            END;
            IF NOT delete_blocked THEN
                RAISE EXCEPTION 'movement trigger accepted a delete';
            END IF;
        END $$
        '''
    )


def migrate(cr, version):
    if not _table_exists(cr, 'loyalty_consign_line'):
        return

    cr.execute('SELECT pg_advisory_xact_lock(%s)', (0x574F4F57,))
    for initial_table in (
        'loyalty_card', 'loyalty_consign_line', 'loyalty_program',
        'product_product', 'product_template',
    ):
        if _table_exists(cr, initial_table):
            cr.execute(
                sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
                    sql.Identifier(initial_table)
                )
            )

    # Direct pre-Task-3 upgrades do not yet have the exact UoM dimension.
    cr.execute(
        'ALTER TABLE loyalty_consign_line ADD COLUMN IF NOT EXISTS product_uom_id integer'
    )
    cr.execute(
        '''
        UPDATE loyalty_consign_line line
           SET product_uom_id = template.uom_id
          FROM product_product product
          JOIN product_template template ON template.id = product.product_tmpl_id
         WHERE line.product_id = product.id AND line.product_uom_id IS NULL
        '''
    )
    if _scalar(
        cr, 'SELECT COUNT(*) FROM loyalty_consign_line WHERE product_uom_id IS NULL'
    ):
        raise RuntimeError('Every legacy consignment projection requires an exact UoM.')
    if _scalar(
        cr,
        '''SELECT COUNT(*) FROM loyalty_consign_line line
            JOIN loyalty_card card ON card.id = line.card_id
            JOIN loyalty_program program ON program.id = card.program_id
           WHERE program.company_id IS NULL''',
    ):
        raise RuntimeError(
            'Legacy consignment projections on company-less programs require '
            'controlled company assignment before Task 4 upgrade.'
        )

    foreign_keys = _foreign_keys(cr)
    required_tables = {
        'loyalty_consign_line', 'loyalty_card', 'loyalty_program',
        'loyalty_consign_operation', 'loyalty_consign_movement',
        'loyalty_consign_hold', 'loyalty_consign_hold_allocation',
        'loyalty_consign_refund_saga',
        'loyalty_consign_redemption', 'loyalty_consign_redemption_line',
        *[row[0] for row in foreign_keys],
    }
    # Hold headers are explicitly locked even though they do not directly
    # reference projections; their state controls allocation authority.
    for table_name in sorted(
        table for table in required_tables if _table_exists(cr, table)
    ):
        cr.execute(
            sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
                sql.Identifier(table_name)
            )
        )

    cr.execute(
        '''
        CREATE TABLE IF NOT EXISTS loyalty_consign_projection_merge_run (
            id bigserial PRIMARY KEY,
            executed_at timestamp without time zone NOT NULL DEFAULT NOW(),
            source_version varchar,
            before_line_count bigint NOT NULL,
            after_line_count bigint,
            before_dimension_count bigint NOT NULL,
            after_dimension_count bigint,
            before_line_qty numeric NOT NULL,
            after_line_qty numeric,
            before_movement_count bigint NOT NULL,
            after_movement_count bigint,
            before_movement_qty numeric NOT NULL,
            after_movement_qty numeric,
            before_movement_value numeric NOT NULL,
            after_movement_value numeric,
            before_allocation_count bigint NOT NULL,
            after_allocation_count bigint,
            before_allocation_qty numeric NOT NULL,
            after_allocation_qty numeric,
            before_redemption_count bigint NOT NULL,
            after_redemption_count bigint,
            before_redemption_qty numeric NOT NULL,
            after_redemption_qty numeric,
            before_redemption_value numeric NOT NULL,
            after_redemption_value numeric,
            trigger_verified boolean NOT NULL DEFAULT FALSE
        )
        '''
    )
    cr.execute(
        '''
        CREATE TABLE IF NOT EXISTS loyalty_consign_projection_merge_map (
            run_id bigint NOT NULL,
            old_line_id bigint NOT NULL,
            survivor_line_id bigint NOT NULL,
            card_id bigint NOT NULL,
            product_id bigint NOT NULL,
            product_uom_id bigint NOT NULL,
            is_survivor boolean NOT NULL,
            old_snapshot jsonb NOT NULL,
            PRIMARY KEY (run_id, old_line_id)
        )
        '''
    )
    cr.execute(
        '''
        CREATE TABLE IF NOT EXISTS loyalty_consign_projection_merge_dimension_audit (
            id bigserial PRIMARY KEY,
            run_id bigint NOT NULL,
            company_id bigint,
            card_id bigint NOT NULL,
            program_id bigint,
            product_id bigint NOT NULL,
            product_uom_id bigint NOT NULL,
            before_line_count bigint NOT NULL,
            after_line_count bigint,
            before_qty_deposited numeric NOT NULL,
            after_qty_deposited numeric,
            before_qty_redeemed numeric NOT NULL,
            after_qty_redeemed numeric,
            before_qty_remaining numeric NOT NULL,
            after_qty_remaining numeric,
            before_amount_deposited numeric NOT NULL,
            after_amount_deposited numeric,
            before_amount_remaining numeric NOT NULL,
            after_amount_remaining numeric
        )
        '''
    )

    before_line_count = _metric(cr, 'loyalty_consign_line', 'COUNT(*)')
    before_dimension_count = _scalar(
        cr, 'SELECT COUNT(*) FROM (SELECT 1 FROM loyalty_consign_line GROUP BY card_id, product_id, product_uom_id) dimensions'
    )
    before_line_qty = _metric(cr, 'loyalty_consign_line', 'COALESCE(SUM(qty_deposited), 0)')
    before_movement_count = _metric(cr, 'loyalty_consign_movement', 'COUNT(*)')
    before_movement_qty = _metric(cr, 'loyalty_consign_movement', 'COALESCE(SUM(quantity), 0)')
    before_movement_value = _metric(cr, 'loyalty_consign_movement', 'COALESCE(SUM(value_delta), 0)')
    before_allocation_count = _metric(cr, 'loyalty_consign_hold_allocation', 'COUNT(*)')
    before_allocation_qty = _metric(cr, 'loyalty_consign_hold_allocation', 'COALESCE(SUM(quantity), 0)')
    before_redemption_count = _metric(cr, 'loyalty_consign_redemption_line', 'COUNT(*)')
    before_redemption_qty = _metric(cr, 'loyalty_consign_redemption_line', 'COALESCE(SUM(qty_redeemed), 0)')
    before_redemption_value = _metric(cr, 'loyalty_consign_redemption_line', 'COALESCE(SUM(subtotal), 0)')
    cr.execute(
        '''
        INSERT INTO loyalty_consign_projection_merge_run (
            source_version, before_line_count, before_dimension_count, before_line_qty,
            before_movement_count, before_movement_qty, before_movement_value,
            before_allocation_count, before_allocation_qty,
            before_redemption_count, before_redemption_qty, before_redemption_value
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        ''',
        (
            version, before_line_count, before_dimension_count, before_line_qty,
            before_movement_count, before_movement_qty, before_movement_value,
            before_allocation_count, before_allocation_qty,
            before_redemption_count, before_redemption_qty, before_redemption_value,
        ),
    )
    run_id = cr.fetchone()[0]

    cr.execute(
        '''
        CREATE TEMP TABLE woow_consign_projection_full_map ON COMMIT DROP AS
        SELECT id AS old_line_id,
               MIN(id) OVER (PARTITION BY card_id, product_id, product_uom_id)
                   AS survivor_line_id
          FROM loyalty_consign_line
        '''
    )
    cr.execute(
        '''CREATE TEMP TABLE woow_consign_projection_duplicate_map ON COMMIT DROP AS
           SELECT * FROM woow_consign_projection_full_map
            WHERE old_line_id <> survivor_line_id'''
    )
    cr.execute(
        '''
        INSERT INTO loyalty_consign_projection_merge_map (
            run_id, old_line_id, survivor_line_id, card_id, product_id,
            product_uom_id, is_survivor, old_snapshot
        )
        SELECT %s, old.id, mapping.survivor_line_id, old.card_id, old.product_id,
               old.product_uom_id, old.id = mapping.survivor_line_id, to_jsonb(old)
          FROM woow_consign_projection_full_map mapping
          JOIN loyalty_consign_line old ON old.id = mapping.old_line_id
      ORDER BY old.id
        ''',
        (run_id,),
    )
    cr.execute(
        '''
        INSERT INTO loyalty_consign_projection_merge_dimension_audit (
            run_id, company_id, card_id, program_id, product_id, product_uom_id,
            before_line_count, before_qty_deposited, before_qty_redeemed,
            before_qty_remaining, before_amount_deposited, before_amount_remaining
        )
        SELECT %s, program.company_id, line.card_id, card.program_id,
               line.product_id, line.product_uom_id, COUNT(*),
               COALESCE(SUM(line.qty_deposited), 0),
               COALESCE(SUM(line.qty_redeemed), 0),
               COALESCE(SUM(line.qty_remaining), 0),
               COALESCE(SUM(line.amount_deposited), 0),
               COALESCE(SUM(line.amount_remaining), 0)
          FROM loyalty_consign_line line
          JOIN loyalty_card card ON card.id = line.card_id
          JOIN loyalty_program program ON program.id = card.program_id
      GROUP BY program.company_id, line.card_id, card.program_id,
               line.product_id, line.product_uom_id
      ORDER BY program.company_id NULLS FIRST, line.card_id,
               card.program_id, line.product_id, line.product_uom_id
        ''',
        (run_id,),
    )

    if _scalar(
        cr,
        '''SELECT COUNT(*)
             FROM woow_consign_projection_full_map mapping
             JOIN loyalty_consign_line old ON old.id = mapping.old_line_id
             JOIN loyalty_consign_line survivor ON survivor.id = mapping.survivor_line_id
            WHERE (old.card_id, old.product_id, old.product_uom_id)
                  IS DISTINCT FROM
                  (survivor.card_id, survivor.product_id, survivor.product_uom_id)''',
    ):
        raise RuntimeError('Consignment projection consolidation dimension mismatch.')
    _assert_ledger_dimensions(cr)

    if _table_exists(cr, 'loyalty_consign_movement'):
        cr.execute(
            sql.SQL('DROP TRIGGER IF EXISTS {} ON loyalty_consign_movement').format(
                sql.Identifier(_TRIGGER)
            )
        )
    for table_name, column_name, _constraint_name in foreign_keys:
        cr.execute(
            sql.SQL(
                '''UPDATE {} reference SET {} = mapping.survivor_line_id
                    FROM woow_consign_projection_duplicate_map mapping
                   WHERE reference.{} = mapping.old_line_id'''
            ).format(
                sql.Identifier(table_name), sql.Identifier(column_name),
                sql.Identifier(column_name),
            )
        )

    # Operation payloads are durable audit envelopes and later become replay
    # identity again in the post-migration re-hash. Repoint only known line-ID
    # paths; a blanket numeric JSON replacement could corrupt unrelated IDs
    # that happen to share the same integer value.
    if (
        _table_exists(cr, 'loyalty_consign_operation')
        and _column_exists(cr, 'loyalty_consign_operation', 'payload_json')
    ):
        cr.execute(
            '''UPDATE loyalty_consign_operation operation
                  SET payload_json = jsonb_set(
                      operation.payload_json, '{source_res_id}',
                      to_jsonb(mapping.survivor_line_id), FALSE
                  )
                 FROM woow_consign_projection_duplicate_map mapping
                WHERE operation.payload_json->>'source_model' = 'loyalty.consign.line'
                  AND operation.payload_json->>'source_res_id' = mapping.old_line_id::text'''
        )
        for payload_key in ('aggregate_line_id', 'line_id'):
            cr.execute(
                sql.SQL(
                    '''UPDATE loyalty_consign_operation operation
                          SET payload_json = jsonb_set(
                              operation.payload_json, {},
                              to_jsonb(mapping.survivor_line_id), FALSE
                          )
                         FROM woow_consign_projection_duplicate_map mapping
                        WHERE operation.payload_json #>> {} = mapping.old_line_id::text'''
                ).format(
                    sql.Literal(['payload', payload_key]),
                    sql.Literal(['payload', payload_key]),
                )
            )

    # Polymorphic source pairs are not discoverable through pg_constraint.
    # Repoint every known installed audit source before deleting old IDs.
    polymorphic_tables = [
        table_name for table_name in (
            'loyalty_consign_operation', 'loyalty_consign_movement',
            'loyalty_consign_hold', 'loyalty_consign_refund_saga',
        ) if _table_exists(cr, table_name)
        and _column_exists(cr, table_name, 'source_model')
        and _column_exists(cr, table_name, 'source_res_id')
    ]
    for table_name in polymorphic_tables:
        cr.execute(
            sql.SQL(
                '''UPDATE {} source SET source_res_id = mapping.survivor_line_id
                    FROM woow_consign_projection_duplicate_map mapping
                   WHERE source.source_model = 'loyalty.consign.line'
                     AND source.source_res_id = mapping.old_line_id'''
            ).format(sql.Identifier(table_name))
        )

    # Merge only columns present in every legacy schema. Task 3/4 shadow fields
    # are rebuilt from immutable movements in the post-migration.
    cr.execute(
        '''
        WITH grouped AS (
            SELECT mapping.survivor_line_id,
                   SUM(line.qty_deposited) qty_deposited,
                   SUM(line.qty_redeemed) qty_redeemed,
                   SUM(line.qty_remaining) qty_remaining,
                   SUM(line.amount_deposited) amount_deposited,
                   SUM(line.amount_remaining) amount_remaining,
                   CASE WHEN SUM(line.qty_deposited) <> 0
                        THEN SUM(line.qty_deposited * line.unit_price)
                             / SUM(line.qty_deposited) ELSE 0 END unit_price,
                   MIN(line.date_deposited) date_deposited,
                   CASE WHEN COUNT(DISTINCT line.product_desc)
                                  FILTER (WHERE line.product_desc IS NOT NULL) = 1
                        THEN MIN(line.product_desc) END product_desc,
                   CASE WHEN COUNT(DISTINCT line.lot_id)
                                  FILTER (WHERE line.lot_id IS NOT NULL) = 1
                                  AND COUNT(*) FILTER (WHERE line.lot_id IS NOT NULL) = COUNT(*)
                        THEN MIN(line.lot_id) END lot_id,
                   CASE WHEN COUNT(DISTINCT line.storage_note)
                                  FILTER (WHERE line.storage_note IS NOT NULL) = 1
                                  AND COUNT(*) FILTER (WHERE line.storage_note IS NOT NULL) = COUNT(*)
                        THEN MIN(line.storage_note) END storage_note,
                   CASE WHEN COUNT(DISTINCT line.sale_line_id)
                                  FILTER (WHERE line.sale_line_id IS NOT NULL) = 1
                             AND COUNT(*) FILTER (WHERE line.sale_line_id IS NULL) = 0
                        THEN MIN(line.sale_line_id) END sale_line_id,
                   BOOL_AND(line.is_cancelled) is_cancelled
              FROM woow_consign_projection_full_map mapping
              JOIN loyalty_consign_line line ON line.id = mapping.old_line_id
          GROUP BY mapping.survivor_line_id
        )
        UPDATE loyalty_consign_line survivor
           SET qty_deposited = grouped.qty_deposited,
               qty_redeemed = grouped.qty_redeemed,
               qty_remaining = grouped.qty_remaining,
               amount_deposited = grouped.amount_deposited,
               amount_remaining = grouped.amount_remaining,
               unit_price = grouped.unit_price,
               date_deposited = grouped.date_deposited,
               product_desc = grouped.product_desc,
               lot_id = grouped.lot_id,
               storage_note = grouped.storage_note,
               sale_line_id = grouped.sale_line_id,
               is_cancelled = grouped.is_cancelled
          FROM grouped WHERE survivor.id = grouped.survivor_line_id
        '''
    )
    cr.execute(
        '''DELETE FROM loyalty_consign_line
            WHERE id IN (
                SELECT old_line_id FROM woow_consign_projection_duplicate_map
            )'''
    )
    cr.execute(
        '''
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'loyalty_consign_line'::regclass
                   AND conname = 'loyalty_consign_line_card_product_uom_unique'
            ) THEN
                ALTER TABLE loyalty_consign_line
                ADD CONSTRAINT loyalty_consign_line_card_product_uom_unique
                UNIQUE (card_id, product_id, product_uom_id);
            END IF;
        END $$
        '''
    )

    after_line_count = _metric(cr, 'loyalty_consign_line', 'COUNT(*)')
    after_dimension_count = _scalar(
        cr, 'SELECT COUNT(*) FROM (SELECT 1 FROM loyalty_consign_line GROUP BY card_id, product_id, product_uom_id) dimensions'
    )
    after_line_qty = _metric(cr, 'loyalty_consign_line', 'COALESCE(SUM(qty_deposited), 0)')
    after_movement_count = _metric(cr, 'loyalty_consign_movement', 'COUNT(*)')
    after_movement_qty = _metric(cr, 'loyalty_consign_movement', 'COALESCE(SUM(quantity), 0)')
    after_movement_value = _metric(cr, 'loyalty_consign_movement', 'COALESCE(SUM(value_delta), 0)')
    after_allocation_count = _metric(cr, 'loyalty_consign_hold_allocation', 'COUNT(*)')
    after_allocation_qty = _metric(cr, 'loyalty_consign_hold_allocation', 'COALESCE(SUM(quantity), 0)')
    after_redemption_count = _metric(cr, 'loyalty_consign_redemption_line', 'COUNT(*)')
    after_redemption_qty = _metric(cr, 'loyalty_consign_redemption_line', 'COALESCE(SUM(qty_redeemed), 0)')
    after_redemption_value = _metric(cr, 'loyalty_consign_redemption_line', 'COALESCE(SUM(subtotal), 0)')

    invariants = (
        (after_line_count, before_dimension_count, 'survivor count'),
        (after_dimension_count, before_dimension_count, 'dimension count'),
        (after_line_qty, before_line_qty, 'legacy issue quantity'),
        (after_movement_count, before_movement_count, 'movement count'),
        (after_movement_qty, before_movement_qty, 'movement quantity'),
        (after_movement_value, before_movement_value, 'movement value'),
        (after_allocation_count, before_allocation_count, 'allocation count'),
        (after_allocation_qty, before_allocation_qty, 'allocation quantity'),
        (after_redemption_count, before_redemption_count, 'redemption count'),
        (after_redemption_qty, before_redemption_qty, 'redemption quantity'),
        (after_redemption_value, before_redemption_value, 'redemption value'),
    )
    for actual, expected, label in invariants:
        equal = actual == expected
        if label == 'legacy issue quantity':
            equal = math.isclose(
                float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-9,
            )
        if not equal:
            raise RuntimeError(
                f'Consignment projection consolidation changed {label}: '
                f'{expected} -> {actual}.'
            )

    _assert_ledger_dimensions(cr)
    for table_name in polymorphic_tables:
        if _scalar(
            cr,
            sql.SQL(
                '''SELECT COUNT(*) FROM {} source
                    JOIN woow_consign_projection_duplicate_map mapping
                      ON mapping.old_line_id = source.source_res_id
                   WHERE source.source_model = 'loyalty.consign.line' '''
            ).format(sql.Identifier(table_name)),
        ):
            raise RuntimeError(
                f'Consolidation left deleted polymorphic line sources in {table_name}.'
            )
    if (
        _table_exists(cr, 'loyalty_consign_operation')
        and _column_exists(cr, 'loyalty_consign_operation', 'payload_json')
        and _scalar(
            cr,
            '''SELECT COUNT(*)
                 FROM loyalty_consign_operation operation
                 JOIN woow_consign_projection_duplicate_map mapping ON (
                     (operation.payload_json->>'source_model' = 'loyalty.consign.line'
                      AND operation.payload_json->>'source_res_id' = mapping.old_line_id::text)
                     OR operation.payload_json #>> '{payload,aggregate_line_id}' = mapping.old_line_id::text
                     OR operation.payload_json #>> '{payload,line_id}' = mapping.old_line_id::text
                 )''',
        )
    ):
        raise RuntimeError(
            'Consolidation left deleted projection IDs in operation payload audit data.'
        )
    for table_name, column_name, _constraint_name in foreign_keys:
        dangling = _scalar(
            cr,
            sql.SQL(
                '''SELECT COUNT(*) FROM {} reference
                    LEFT JOIN loyalty_consign_line line ON line.id = reference.{}
                   WHERE reference.{} IS NOT NULL AND line.id IS NULL'''
            ).format(
                sql.Identifier(table_name), sql.Identifier(column_name),
                sql.Identifier(column_name),
            ),
        )
        if dangling:
            raise RuntimeError(
                f'Consignment consolidation left {dangling} dangling references '
                f'in {table_name}.{column_name}.'
            )

    cr.execute(
        '''
        WITH after_group AS (
            SELECT program.company_id, line.card_id, card.program_id,
                   line.product_id, line.product_uom_id, COUNT(*) line_count,
                   COALESCE(SUM(line.qty_deposited), 0) qty_deposited,
                   COALESCE(SUM(line.qty_redeemed), 0) qty_redeemed,
                   COALESCE(SUM(line.qty_remaining), 0) qty_remaining,
                   COALESCE(SUM(line.amount_deposited), 0) amount_deposited,
                   COALESCE(SUM(line.amount_remaining), 0) amount_remaining
              FROM loyalty_consign_line line
              JOIN loyalty_card card ON card.id = line.card_id
              JOIN loyalty_program program ON program.id = card.program_id
          GROUP BY program.company_id, line.card_id, card.program_id,
                   line.product_id, line.product_uom_id
        )
        UPDATE loyalty_consign_projection_merge_dimension_audit audit
           SET after_line_count = grouped.line_count,
               after_qty_deposited = grouped.qty_deposited,
               after_qty_redeemed = grouped.qty_redeemed,
               after_qty_remaining = grouped.qty_remaining,
               after_amount_deposited = grouped.amount_deposited,
               after_amount_remaining = grouped.amount_remaining
          FROM after_group grouped
         WHERE audit.run_id = %s
           AND audit.company_id IS NOT DISTINCT FROM grouped.company_id
           AND audit.card_id = grouped.card_id
           AND audit.program_id IS NOT DISTINCT FROM grouped.program_id
           AND audit.product_id = grouped.product_id
           AND audit.product_uom_id = grouped.product_uom_id
        ''',
        (run_id,),
    )
    if _scalar(
        cr,
        '''SELECT COUNT(*)
             FROM loyalty_consign_projection_merge_dimension_audit
            WHERE run_id = %s AND (
                after_line_count IS NULL
                OR before_line_count < after_line_count
                OR before_qty_deposited <> after_qty_deposited
                OR before_qty_redeemed <> after_qty_redeemed
                OR before_qty_remaining <> after_qty_remaining
                OR before_amount_deposited <> after_amount_deposited
                OR before_amount_remaining <> after_amount_remaining
            )''',
        (run_id,),
    ):
        raise RuntimeError('Dimension-group projection audit did not reconcile exactly.')

    _verify_unique_constraint(cr)
    _ensure_trigger(cr)
    trigger_verified = not _table_exists(cr, 'loyalty_consign_movement')
    if not trigger_verified:
        trigger_verified = _scalar(
            cr,
            '''SELECT COUNT(*) = 1 FROM pg_trigger
                WHERE tgname = %s
                  AND tgrelid = 'loyalty_consign_movement'::regclass
                  AND NOT tgisinternal''',
            (_TRIGGER,),
        )
        if not trigger_verified:
            raise RuntimeError('Movement immutability trigger was not restored.')
        _verify_trigger_rejects_mutation(cr)

    cr.execute(
        '''
        UPDATE loyalty_consign_projection_merge_run
           SET after_line_count = %s, after_dimension_count = %s, after_line_qty = %s,
               after_movement_count = %s, after_movement_qty = %s,
               after_movement_value = %s, after_allocation_count = %s,
               after_allocation_qty = %s, after_redemption_count = %s,
               after_redemption_qty = %s, after_redemption_value = %s,
               trigger_verified = %s
         WHERE id = %s
        ''',
        (
            after_line_count, after_dimension_count, after_line_qty,
            after_movement_count, after_movement_qty, after_movement_value,
            after_allocation_count, after_allocation_qty,
            after_redemption_count, after_redemption_qty, after_redemption_value,
            trigger_verified, run_id,
        ),
    )
