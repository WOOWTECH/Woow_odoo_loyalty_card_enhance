def migrate(cr, version):
    """Backfill the legacy line UoM before the ORM makes it required."""
    cr.execute(
        """
        ALTER TABLE loyalty_consign_line
        ADD COLUMN IF NOT EXISTS product_uom_id integer
        """
    )
    cr.execute(
        """
        UPDATE loyalty_consign_line AS line
           SET product_uom_id = template.uom_id
          FROM product_product AS product
          JOIN product_template AS template
            ON template.id = product.product_tmpl_id
         WHERE line.product_id = product.id
           AND line.product_uom_id IS NULL
        """
    )
