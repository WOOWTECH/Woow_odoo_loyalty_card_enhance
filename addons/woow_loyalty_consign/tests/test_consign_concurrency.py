import inspect
import os
from pathlib import Path

from odoo.tests.common import TransactionCase

from odoo.addons.woow_loyalty_consign.models.loyalty_consign_engine import (
    LoyaltyConsignEngine,
)
from odoo.addons.woow_loyalty_consign.models.loyalty_consign_hold import (
    LoyaltyConsignHold,
    _MAX_EXPIRY_BATCH_SIZE,
    _MAX_EXPIRY_CANDIDATE_SCAN,
    _expiry_candidate_scan_limit,
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
        movement_lock = engine[issue:allocation]
        self.assertIn('ORDER BY id\n                  FOR UPDATE', movement_lock)
        self.assertNotIn('ORDER BY occurred_at, id\n                  FOR UPDATE', movement_lock)
        self.assertIn('_open_command(', engine)
        self.assertIn('_authorization_allocation_plan', engine)

    def test_capture_lifecycle_locks_original_operations_before_projections(self):
        source = inspect.getsource(LoyaltyConsignHold._lock_active_lifecycle_dimensions)
        original_operation = source.index('movement_model._lock_original_operation_token')
        card = source.index('UPDATE loyalty_card SET write_date = write_date')
        projection = source.index('UPDATE loyalty_consign_line SET write_date = write_date')
        hold = source.index('SELECT id FROM loyalty_consign_hold')
        issue = source.index('SELECT id FROM loyalty_consign_movement')
        allocation = source.index('SELECT id FROM loyalty_consign_hold_allocation')
        self.assertIn('TASK6_CAPTURE_CLAWBACK_LOCK_ORDER', source)
        self.assertLess(original_operation, card)
        self.assertLess(card, projection)
        self.assertLess(projection, hold)
        self.assertLess(hold, issue)
        self.assertLess(issue, allocation)

    def test_expiry_contract_uses_bounded_skip_locked_selection(self):
        source = inspect.getsource(LoyaltyConsignHold._cron_expire_holds)
        self.assertIn('batch_size', source)
        self.assertIn('candidate_scan_limit = _expiry_candidate_scan_limit(batch_size)', source)
        lock_source = inspect.getsource(LoyaltyConsignHold._lock_expiry_candidates)
        self.assertIn('FOR UPDATE SKIP LOCKED', lock_source)
        self.assertIn("state = 'active'", source)
        self.assertIn('ORDER BY expires_at, id', source)
        self.assertIn('expires_at <=', source)
        self.assertIn('_probe_expiry_candidate', source)
        self.assertIn('while remaining:', source)
        self.assertIn('_reconcile_projection', source)
        self.assertIn("'transition_user_id': self.env.uid", source)
        probe = inspect.getsource(LoyaltyConsignHold._probe_expiry_candidate)
        self.assertIn('with self.env.cr.savepoint()', probe)
        self.assertIn('raise _ReleaseExpiryProbe()', probe)
        self.assertIn('except _ReleaseExpiryProbe:', probe)
        self.assertIn('except _ExpiryCandidateUnavailable:', probe)

    def test_expiry_candidate_window_exceeds_maximum_completion_batch(self):
        self.assertEqual(_MAX_EXPIRY_BATCH_SIZE, 1000)
        self.assertEqual(_MAX_EXPIRY_CANDIDATE_SCAN, 10000)
        self.assertGreater(
            _expiry_candidate_scan_limit(_MAX_EXPIRY_BATCH_SIZE),
            _MAX_EXPIRY_BATCH_SIZE,
        )
        self.assertLessEqual(
            _expiry_candidate_scan_limit(_MAX_EXPIRY_BATCH_SIZE),
            _MAX_EXPIRY_CANDIDATE_SCAN,
        )

    def test_real_two_cursor_probes_are_executable_and_cover_required_races(self):
        addon_root = Path(inspect.getfile(LoyaltyConsignEngine)).parents[1]
        requirements = {
            'task5_authorize_concurrency_probe.py': (
                'TASK5_CONCURRENCY_PROBE=PASS',
                'TASK5_SAME_KEY_REPLAY=PASS',
                'TASK5_AUTHORIZE_REVERSAL_RACE=PASS',
            ),
            'task6_lifecycle_concurrency_probe.py': (
                'TASK6_CONCURRENCY_PROBE=PASS',
                'TASK6_CAPTURE_EXPIRY_STALE_FENCE=PASS',
                'TASK6_RELEASE_EXPIRY_STALE_FENCE=PASS',
                'TASK6_SAME_KEY_CAPTURE_REPLAY=PASS',
                'TASK6_CLAWBACK_AUTHORIZE_STALE_FENCE=PASS',
                'TASK6_CAPTURE_CLAWBACK_LOCK_ORDER=PASS',
                'TASK6_CAPTURE_CLAWBACK_STALE_FENCE=PASS',
                'TASK6_CAPTURE_CLAWBACK_SAFE_RETRY=PASS',
                'TASK6_CAPTURE_CLAWBACK_NO_OVERCONSUME=PASS',
            ),
            'task6_upgrade_lifecycle_probe.py': (
                'TASK6_UPGRADE_PREPARE=PASS',
                'TASK6_UPGRADE_ACTIVE_CAPTURED_RELEASED_EXPIRED=PASS',
                'TASK6_UPGRADE_LEDGER_PROJECTION=PASS',
                'TASK6_UPGRADE_DEACTIVATION_GUARD=PASS',
                'TASK6_UPGRADE_CAPTURE_RELEASE_REPLAY=PASS',
                'TASK6_UPGRADE_LIFECYCLE_PROBE=PASS',
            ),
        }
        for filename, markers in requirements.items():
            with self.subTest(filename=filename):
                probe = addon_root / 'tests' / 'probes' / filename
                self.assertTrue(probe.exists())
                self.assertTrue(os.access(probe, os.X_OK))
                source = probe.read_text()
                self.assertIn('refuses a non-test database', source)
                for marker in markers:
                    self.assertIn(marker, source)
                if filename.endswith('_concurrency_probe.py'):
                    self.assertIn('SerializationFailure', source)
                    self.assertIn('ValidationError', source)
