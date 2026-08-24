LEGACY_POS_ACL_XMLIDS = (
    'access_loyalty_consign_line_pos_user',
    'access_loyalty_consign_redemption_pos_user',
    'access_loyalty_consign_redemption_line_pos_user',
)


def migrate(cr, version):
    """Drop ACLs that moved from the core addon to the POS bridge."""
    cr.execute(
        """
        DELETE FROM ir_model_access AS access
        USING ir_model_data AS data
        WHERE access.id = data.res_id
          AND data.module = 'woow_loyalty_consign'
          AND data.model = 'ir.model.access'
          AND data.name IN %s
        """,
        (LEGACY_POS_ACL_XMLIDS,),
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
        WHERE module = 'woow_loyalty_consign'
          AND model = 'ir.model.access'
          AND name IN %s
        """,
        (LEGACY_POS_ACL_XMLIDS,),
    )
