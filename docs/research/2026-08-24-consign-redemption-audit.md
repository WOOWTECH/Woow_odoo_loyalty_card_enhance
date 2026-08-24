# Consignment Redemption Support Audit — 2026-08-24

## Scope

Audited current `main` (`3bc3ee1`) across:

- core/manual ledger: `woow_loyalty_consign`;
- Sales issuance at quotation confirmation;
- optional POS bridge: `woow_loyalty_consign_pos`;
- portal exposure;
- native Odoo 18 `website_sale_loyalty` checkout seams;
- live MujiMed module/channel inventory (read-only);
- isolated Odoo 18 diagnostic databases only for mutation tests.

No diagnostic record was created in the MujiMed production database.

## Live MujiMed inventory

- `website_sale` and `website_sale_loyalty`: installed.
- `woow_loyalty_consign`: installed (`18.0.1.2.0`).
- `woow_consign_booking`: installed (`18.0.2.0.0`), booking redemption retired.
- `point_of_sale`, `pos_loyalty`, `woow_loyalty_consign_pos`: uninstalled.
- Existing consign data: 3 cards, 7 lines, deposited quantity 40, redeemed quantity 0, no redemptions.

Therefore MujiMed currently has backend/manual support and read-only portal data, but no active POS channel and no eCommerce redemption channel.

## Executed evidence

Diagnostic tests are tagged `consign_audit` and excluded from normal `standard` tests.

### Core/manual/Sales

- 17 diagnostic methods.
- Known-good controls passed: ordinary redemption, over-balance rejection, manual wizard redemption, single-program Sales issuance, and observed Sales cancellation behavior.
- Required invariants currently produce 10 failures and 2 errors.

Confirmed defects:

1. Duplicate redemption lines can redeem 12 from a balance of 10.
2. Completed redemption lines remain writable.
3. Completed redemptions remain deletable.
4. Inactive cards can still be redeemed.
5. Non-consign cards can own consign lines.
6. Replaying the same manual wizard redeems twice.
7. Portal users can search another customer's `loyalty.card`.
8. A newly created consign line can fail immediate redemption because stored `qty_redeemed` is still SQL `NULL`, causing `float - NoneType`.
9. The default creation email fails QWeb rendering because `loyalty.consign.line.date_expiry` does not exist.
10. The PDF template references the same nonexistent expiry field/state and is expected to fail similarly.
11. Directly writing `trigger_product_ids` on a consign program with no rule silently persists no trigger products; the field is related to `rule_ids.product_ids`.
12. Two triggered programs on one order do not both receive an explicit allocation; processing order decides the winner.
13. Merging same-product/same-price deposits loses later sale-line provenance.
14. Cancelling a confirmed sale leaves issued balance active; there is no reversal lifecycle.

### POS backend

- 8 diagnostic methods.
- Two lookup controls passed.
- Five required invariants failed and one POS-only cashier case errored.

Confirmed defects:

1. Barcode JS always sends `partner_id=false`; backend always rejects a found card without a selected partner.
2. Final confirmation does not verify POS order customer owns the card.
3. Final confirmation does not derive/check payload against persisted POS order lines.
4. Inactive cards can be redeemed through final POS confirmation.
5. Repeated post-push callbacks create duplicate redemptions.
6. A real internal POS-only cashier reaches `action_done()` but lacks write ACL on `loyalty.consign.redemption`.
7. POS order sync/payment happens before separate redemption RPC; failure only raises an alert and is not durable/retryable.
8. UI payload is stored separately from POS lines; line deletion/customer changes do not reconcile it.
9. Fractional quantities are broken by integer increment and `parseInt`.

### Installation

A clean DB with only `--init=woow_loyalty_consign` exits 255. Core ACL CSV references `point_of_sale.group_pos_user` but the manifest does not depend on POS. POS-specific ACL/data must move to the POS bridge, or core must explicitly depend on POS.

### Concurrency

Two independent transactions each attempted to redeem 6 from 10:

- one completed;
- one raised PostgreSQL `SerializationFailure`;
- final balance was redeemed 6, remaining 4.

The row lock prevents overspending between separate requests, but callers need transaction retry and idempotency. It does not protect against duplicate source lines inside one redemption.

## eCommerce gap and native extension seam

Current consign programs are explicitly excluded from native Sales loyalty management and define no reward/controller/cart asset. There is no website redemption path.

Odoo 18 native flow:

1. cart page refreshes programs/rewards;
2. `/shop/claimreward` obtains the current website order;
3. server re-resolves reward/card against `_get_claimable_and_showable_rewards()`;
4. `sale.order._apply_program_reward()` creates native tagged reward lines;
5. payment validation refreshes rewards and rejects payment if order total changed.

Recommended boundary:

- separate `woow_loyalty_consign_website_sale` addon;
- thin cart UI/controller;
- server-authoritative desired allocation on `sale.order`;
- never trust browser card, quantity, price, discount or balance;
- use native reward-line metadata/recompute where possible;
- final redemption through one shared aggregate-and-lock command with source relation and idempotency key;
- payment validation must atomically revalidate entitlement;
- manual and POS should migrate to the same command after ledger hardening.

## Priority before website implementation

### P0 shared-ledger foundation

1. Separate POS dependency from core.
2. Fix trigger-product persistence.
3. Fix missing expiry template/report fields.
4. Aggregate/reject duplicate redemption source lines.
5. Make completed ledger records immutable; add controlled reversal entries.
6. Validate active consign card, partner/company, source and UoM.
7. Add source type/reference and unique idempotency key.
8. Add owner rule for portal `loyalty.card`.

### P1 channel adapters

1. Move manual wizard to shared command.
2. Rebuild POS around persisted order lines and same-transaction/durable processing.
3. Add website Cart adapter and payment-aware finalization.
4. Add Sales issuance movement provenance and cancellation/refund reversal.

## Test artifacts

- `addons/woow_loyalty_consign/tests/test_redemption_audit.py`
- `addons/woow_loyalty_consign_pos/tests/test_pos_redemption_audit.py`
- Remote redacted logs: `/root/mujimed-consign-audit/` on the K3s admin node.

These tests intentionally encode currently missing invariants and remain red until implementation hardens each path.
