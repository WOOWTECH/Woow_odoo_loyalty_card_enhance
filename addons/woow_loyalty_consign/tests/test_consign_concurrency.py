import inspect
import os
from pathlib import Path

from odoo.tests.common import TransactionCase

from odoo.addons.woow_loyalty_consign.models.loyalty_consign_engine import (
    LoyaltyConsignEngine,
)
from odoo.addons.woow_loyalty_consign.models.loyalty_consign_hold import (
    LoyaltyConsignHold,
)


class TestConsignConcurrencyContract(TransactionCase):
    """Standard-safe gates; real cursor interleavings live in the shell probe."""

    def test_authorization_lock_hierarchy_and_durable_fences_are_checked_in(self):
        engine = inspect.getsource(LoyaltyConsignEngine)
        card = engine.index('UPDATE loyalty_card SET write_date = write_date')
        projection = engine.index('UPDATE loyalty_consign_line SET write_date = write_date')
        hold = engine.index("FROM loyalty_consign_hold\n                WHERE state = 'active'")
        issue = engine.index('FROM loyalty_consign_movement')
        allocation = engine.index('FROM loyalty_consign_hold_allocation', issue)
        self.assertLess(card, projection)
        self.assertLess(projection, hold)
        self.assertLess(hold, issue)
        self.assertLess(issue, allocation)
        self.assertIn('_open_command(', engine)
        self.assertIn('_authorization_allocation_plan', engine)

    def test_expiry_contract_uses_bounded_skip_locked_selection(self):
        source = inspect.getsource(LoyaltyConsignHold._cron_expire_holds)
        self.assertIn('batch_size', source)
        self.assertIn('FOR UPDATE SKIP LOCKED', source)
        self.assertIn("state = 'active'", source)
        self.assertIn('expires_at <=', source)
        self.assertIn('_reconcile_projection', source)
        self.assertIn("'transition_user_id': self.env.uid", source)

    def test_real_two_cursor_probe_is_executable_and_covers_required_races(self):
        addon_root = Path(inspect.getfile(LoyaltyConsignEngine)).parents[1]
        probe = addon_root / 'tests' / 'probes' / 'task5_authorize_concurrency_probe.py'
        self.assertTrue(probe.exists())
        self.assertTrue(os.access(probe, os.X_OK))
        source = probe.read_text()
        for marker in (
            'SerializationFailure', 'ValidationError',
            'TASK5_DISTINCT_KEYS_STALE_FENCE=PASS',
            'TASK5_FRESH_RETRY_DOMAIN_ERROR=PASS',
            'TASK5_ONE_HOLD_AVAILABLE_FOUR=PASS',
            'TASK5_SAME_KEY_REPLAY=PASS',
            'TASK5_AUTHORIZE_REVERSAL_RACE=PASS',
            'TASK5_EXPIRY_SKIP_LOCKED_IDEMPOTENT=PASS',
            'TASK5_NO_UNIQUE_OR_RAW_ERROR=PASS',
            'refuses a non-test database',
        ):
            self.assertIn(marker, source)
