from odoo import SUPERUSER_ID, api

from odoo.addons.woow_loyalty_consign.hooks import backfill_consign_movements


def migrate(cr, version):
    """Create immutable shadow facts once; exact operation keys make reruns safe."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    backfill_consign_movements(env)
