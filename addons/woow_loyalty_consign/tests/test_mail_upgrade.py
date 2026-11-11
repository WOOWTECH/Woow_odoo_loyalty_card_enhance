"""A tenant's noupdate deposit mail must survive a safe module upgrade."""

import importlib.util
from pathlib import Path

from lxml import etree

from odoo.tests.common import TransactionCase


class TestConsignMailUpgrade(TransactionCase):

    def test_legacy_expiry_column_is_removed_without_losing_other_columns(self):
        path = (
            Path(__file__).resolve().parents[1]
            / 'migrations' / '18.0.8.0.1' / 'post-migration.py'
        )
        spec = importlib.util.spec_from_file_location('consign_mail_upgrade', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        old = '''<div><table><thead><tr>
            <th>Item</th><th>Remaining</th><th>Expiry</th>
            </tr></thead><tbody>
            <t t-foreach="object.consign_line_ids.filtered(lambda l: l.state != 'cancelled')" t-as="line">
            <tr><td><t t-out="line.product_desc"/></td>
            <td><t t-out="line.qty_remaining"/></td>
            <td><t t-if="line.x_expiry_date"><t t-out="line.x_expiry_date"/></t></td>
            </tr></t></tbody></table></div>'''

        repaired = migration._repair_legacy_body(old)
        root = etree.fromstring(repaired.encode())
        self.assertEqual(len(root.xpath('.//th')), 2)
        self.assertEqual(len(root.xpath('.//td')), 2)
        self.assertIn('object._consign_recipient_balance_lines()', repaired)
        self.assertIn('line.qty_available', repaired)
        self.assertNotIn('x_expiry_date', repaired)
        self.assertEqual(migration._repair_legacy_body(repaired), repaired)
