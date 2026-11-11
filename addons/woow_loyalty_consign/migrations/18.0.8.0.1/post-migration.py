"""Repair legacy deposit-mail bodies that refer to a removed expiry field.

The template is noupdate so a normal module upgrade does not refresh old
copies. Preserve each tenant's remaining wording and translations; only the
obsolete expiry column and stale balance expressions are changed.
"""

import logging

from lxml import etree
from psycopg2.extras import Json


_logger = logging.getLogger(__name__)


def _repair_legacy_body(body):
    if 'line.x_expiry_date' not in body:
        return body

    root = etree.fromstring(body.encode('utf-8'))
    old_nodes = root.xpath(
        ".//*[@t-if='line.x_expiry_date' or @t-out='line.x_expiry_date']"
    )
    cells = set()
    for node in old_nodes:
        cell = next((parent for parent in node.iterancestors() if parent.tag == 'td'), None)
        if cell is None:
            raise ValueError('obsolete expiry expression is outside a table cell')
        cells.add(cell)
    if not cells:
        raise ValueError('obsolete expiry expression was not recognized')

    for cell in cells:
        row = cell.getparent()
        data_cells = row.findall('td')
        index = data_cells.index(cell)
        table = next((parent for parent in row.iterancestors() if parent.tag == 'table'), None)
        headings = table.xpath('./thead/tr/th') if table is not None else []
        if len(headings) != len(data_cells):
            raise ValueError('expiry table has unmatched header and data columns')
        headings[index].getparent().remove(headings[index])
        row.remove(cell)

    for node in root.xpath('.//*[@t-foreach]'):
        expression = node.get('t-foreach')
        if expression.startswith('object.consign_line_ids.filtered('):
            node.set('t-foreach', 'object._consign_recipient_balance_lines()')
    for node in root.xpath(".//*[@t-out='line.qty_remaining']"):
        node.set('t-out', 'line.qty_available')
    return etree.tostring(root, encoding='unicode')


def migrate(cr, version):
    cr.execute(
        """
        SELECT t.id, t.body_html
          FROM mail_template t
          JOIN ir_model_data d
            ON d.model = 'mail.template' AND d.res_id = t.id
         WHERE d.module = 'woow_loyalty_consign'
           AND d.name = 'mail_template_consign_card'
         FOR UPDATE OF t
        """
    )
    row = cr.fetchone()
    if not row:
        return
    template_id, translations = row
    changed = False
    repaired = dict(translations or {})
    for language, body in repaired.items():
        try:
            updated = _repair_legacy_body(body)
        except (etree.XMLSyntaxError, ValueError) as error:
            _logger.warning(
                'Consignment mail template %s language %s needs manual repair: %s',
                template_id, language, error,
            )
            continue
        if updated != body:
            repaired[language] = updated
            changed = True
    if changed:
        cr.execute(
            'UPDATE mail_template SET body_html = %s WHERE id = %s',
            (Json(repaired), template_id),
        )
        _logger.info('Repaired legacy consignment mail template %s', template_id)
