from odoo.addons.woow_loyalty_consign_pos.hooks import (
    remove_legacy_core_pos_acls,
)


def _xmlid_res_id(cr, module, name):
    cr.execute(
        """
        SELECT res_id
          FROM ir_model_data
         WHERE module = %s
           AND name = %s
           AND model = 'product.product'
         LIMIT 1
        """,
        (module, name),
    )
    row = cr.fetchone()
    return row[0] if row else None


def _archive_duplicate_redemption_product(cr):
    legacy_product_id = _xmlid_res_id(
        cr, 'woow_loyalty_consign', 'consign_redemption_product',
    )
    duplicate_product_id = _xmlid_res_id(
        cr, 'woow_loyalty_consign_pos', 'consign_pos_redemption_product',
    )
    if not legacy_product_id or not duplicate_product_id:
        return
    if legacy_product_id == duplicate_product_id:
        return

    cr.execute(
        """
        UPDATE product_template
           SET active = FALSE
         WHERE id IN (
             SELECT product_tmpl_id
               FROM product_product
              WHERE id = %s
         )
        """,
        (duplicate_product_id,),
    )


def migrate(cr, version):
    """Prepare legacy records before the POS bridge data is reloaded."""
    remove_legacy_core_pos_acls(cr)
    _archive_duplicate_redemption_product(cr)
