# Consignment redemption: HAOS validation and staged rollout runbook

> **Scope:** non-production validation and release decision gates only. This runbook never authorizes production deployment, a feature enablement, a data reset, or a modification to live addons.

## Boundaries and authority

- The ledger engine remains the only authority for issue, Hold, capture, release, reversal, clawback, and adjustment. Channel adapters must not write movements or balances directly.
- Posted movements are append-only. A `SerializationFailure` is not converted to success; Odoo retries it.
- Website redemption is disabled by default through `website.consign_redemption_enabled`. POS remains uninstalled/disabled until its separately approved phase.
- Use HAOS Odoo 18 only for the validation procedures below. Test source is copied to a versioned directory under the Odoo container's `/tmp`; never replace an installed addon root.
- Test databases, logs, and versioned `/tmp` copies are retained when an operator requests retention. Do not delete them as ordinary cleanup.

## HAOS validation environment

| Item | Required state |
| --- | --- |
| Odoo | Odoo 18 container, separate from any live environment |
| Test database | Named `test_consign_<purpose>_<date>`; never use the configured primary database |
| Addon source | Versioned `/tmp/consign-validation-addons-<git-sha>` copy |
| Logs | Persist Odoo command log and server log with the git SHA and DB name |
| Network/credentials | No production credentials, payment credentials, or customer data in artifacts |

Before each run, record: git SHA, Odoo version, database name, addon path, module versions, command line, start/end time, and operator.

## Test gates

Run these gates in order against a dedicated test DB. A failure blocks the next rollout decision; do not compensate by editing posted movements or database rows.

1. **Clean install:** core, Booking bridge, POS adapter, Website addon, and all manifest dependencies install with no registry or view/ACL error.
2. **Core contract:** ledger issue/authorize/capture/release/reversal/clawback/adjustment tests, SQL movement immutability, idempotency replay, FIFO, and two-cursor locking probes pass.
3. **Channel isolation:** Booking cannot invoke redemption authority; POS and Website only use private server-side engine commands.
4. **Website:** authenticated ownership/company checks; cart mutation releases stale Holds; all-or-nothing authorization; payment `done` captures exactly once; `error`/`cancel` releases; stale/duplicate callback fencing; zero-total behavior.
5. **Sales/Portal:** paid-invoice issuance, manager-only backend adjustments, portal ownership/company record rules, authoritative balance rendering.
6. **Upgrade:** upgrade a fixture from the prior accepted schema through the current module versions, including legacy completed redemption audit linkage. Validate movements, projections, Holds, report/email rendering, and HTTP health.
7. **Security/review:** direct access/RPC denial checks and independent spec/quality review have no unresolved P0/P1 finding.

Store command output and concise PASS/FAIL counts. Test failures caused by outdated fixtures must be corrected without weakening company, ownership, payment, append-only, or locking invariants.

## Feature-disabled upgrade checklist

This checklist applies to a future approved target environment; it is not an instruction to change one now.

- [ ] Explicit deployment approval identifies target, database, module versions, operator, and rollback owner.
- [ ] Fresh baseline inventory records programs, cards, lines, redemptions, movements, projections, and active Holds. Never reuse historical counts.
- [ ] Website flag `website.consign_redemption_enabled` is verified false for every relevant company/website.
- [ ] POS addon is absent or its configuration is disabled.
- [ ] Verified timestamped database backup and addon archive exist; checksum and restore procedure are recorded.
- [ ] Upgrade is first run in a clone/test DB with the exact addon archive.
- [ ] Core upgrade passes before optional Website/POS addon installation.
- [ ] Post-upgrade reconciliation proves projection availability equals ledger availability minus active Holds.
- [ ] `active_hold_count == 0` or every remaining Hold has an approved owner and expiry plan.
- [ ] `/web/health`, report/email rendering, ACL checks, and migration probes pass.
- [ ] No feature is enabled merely because module installation succeeded.

## Monitoring and exception handling

Monitor at least: failed operation journal entries, duplicate callback fences, expired/active Hold counts and age, capture/release/reversal error rate, payment callback failures, projection reconciliation mismatch, SQL trigger rejection, and HTTP health.

- **Payment callback error:** preserve callback ID and source transaction reference; retry only through the channel's trusted server flow. Do not manually create a movement to "finish" payment.
- **Stuck active Hold:** run the approved Hold expiry/release command; record the original operation and resulting release operation. Never deactivate a card to bypass a Hold.
- **Incorrect captured redemption:** use linked `_reverse_redeem()`; do not edit/delete the `redeem` movement.
- **Incorrect issued entitlement:** use `_clawback_issue()` only for unconsumed, non-held quantity.
- **Projection mismatch:** block feature enablement, retain evidence, and run the approved reconciliation/repair procedure in a test clone first.

## Staged enablement decision gates

### Gate A — source and HAOS validation

Required: all test gates above pass; test evidence is retained; reviewers approve; website flag remains false; POS remains disabled. Outcome is **ready for a separately approved target upgrade**, not deployment.

### Gate B — disabled-feature target upgrade

Required: explicit target approval, verified backup/rollback, disabled flags, post-upgrade reconciliation, health check, and no unexpected business-data mutation. Outcome is **eligible for a controlled Website pilot**.

### Gate C — Website pilot

Enable one approved website/company only. Use a designated test customer/card to exercise paid, partial coverage, failed payment, Hold expiry, duplicate callback, zero-total, and refund scenarios. Monitor for the agreed soak window. Any ledger/projection mismatch, unauthorized access, duplicate capture, or failed rollback moves the state to rollback.

### Gate D — POS pilot

Only after Website/core soak passes: install/enable the POS adapter for one approved test configuration, require online authorization, and test scan/Hold/payment/capture/retry. Offline redemption remains rejected. Expand only after the same monitoring/reconciliation gates pass.

## Rollback

Rollback is a release decision, not an edit to ledger history.

1. Disable Website/POS feature entry points first; retain all movements and operations.
2. Preserve logs, callback references, active-Hold inventory, and reconciliation output.
3. Release active Holds only through controlled engine lifecycle operations.
4. Restore code/database only from the verified pre-upgrade backup under the approved incident procedure.
5. Reconcile the restored database before reopening any channel.
6. Record incident owner, scope, timestamps, customer impact, and the condition required to retry rollout.

## Prohibited actions

- No direct SQL mutation/deletion of posted movements, operations, or balances.
- No use of browser/RPC input as ledger authority.
- No use of a production database for test-only reset, fixture creation, or concurrency probes.
- No overwrite of live addon roots with a test archive.
- No rollout, feature enablement, backup deletion, or production data reset without explicit approval.
