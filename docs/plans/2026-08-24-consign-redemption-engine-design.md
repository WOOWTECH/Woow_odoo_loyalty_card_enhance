# Consignment Redemption Engine and eCommerce Design

**Status:** Accepted

**Date:** 2026-08-24

**Scope:** `woow_loyalty_consign`, backend redemption, Sales issuance, a new website adapter, and the existing POS adapter.

## Problem

The current module has independent and unsafe write paths. A backend redemption document mutates computed balances; Sales grants entitlement when an order is merely confirmed; POS submits an untrusted payload after the paid order has already synchronized; portal users can search other customers' cards; and no website redemption path exists. Executable audit tests also demonstrate duplicate-line over-redemption, mutable/deletable completed entries, missing idempotency, an installation dependency leak, and broken expiry template references.

The system needs one server-authoritative seam that every channel uses. Cart and POS interfaces must be adapters, not alternate balance implementations.

## Chosen Architecture

Use an append-only movement ledger behind a deep `loyalty.consign.engine` module interface.

Public operations:

- `issue`: grant entitlement after verified payment;
- `authorize`: atomically create a 30-minute Hold;
- `capture`: convert an active Hold to redemption movements;
- `release`: release failed, cancelled, or expired Holds;
- `reverse`: restore redeemed quantity after a successful refund;
- `adjust`: manager-only correction with a mandatory reason.

All operations accept a trusted source record, normalized requested allocations, and an idempotency key. The engine owns owner/company/card/product/UoM validation, request aggregation, deterministic locking, FIFO allocation, operation replay, and all-or-nothing behavior. Callers cannot provide price, remaining balance, or actual issue movement allocation.

## Data Model

### `loyalty.consign.line`

One aggregate projection per card, exact `product.product`, and UoM. It exposes issued, redeemed, reversed, held, and available quantities. It is not a ledger and is not directly writable by users. During the first compatibility release it retains read-only aliases used by installed addons and views (`qty_deposited`, `qty_redeemed`, `qty_remaining`, `unit_price`, amount/state fields, and historical redemption relations). `qty_remaining` means posted balance; new `qty_available` additionally subtracts active Holds. `woow_consign_booking`, `woow_mc_consign`, all core views, email, and report consumers are upgraded and tested in the same release before any alias can later be removed.

### `loyalty.consign.operation`

A command journal header. It stores operation type, company, partner, source model/ID, idempotency key, normalized payload hash, state, result, and exception metadata. The engine takes a transaction advisory lock derived from company/key before select-or-create, so simultaneous retries serialize and return one operation instead of colliding on a unique constraint. A replay with the same key and payload returns the existing result; the same key with a different payload is rejected. Pure validation failures occur before creating an operation. Failures that must survive a provider callback are persisted by an outer saga/job without re-raising in the same transaction, because a propagated Odoo exception would roll back its own journal/activity rows.

### `loyalty.consign.movement`

An append-only entry with type `issue`, `redeem`, `redeem_reversal`, `issue_reversal`, `adjustment_in`, or `adjustment_out`. It records company, card, aggregate line, quantity, UoM, source, operation, and the original issue/redeem movement where applicable. `redeem_reversal` restores a returned covered unit; `issue_reversal` claws back an unused entitlement after its package is refunded. Posted entries cannot be updated or deleted. Corrections are additional movements.

Each issue movement remains independent even when the UI shows a product-level aggregate. This preserves the originating SO/SO line and permits FIFO and exact reversal.

### `loyalty.consign.hold`

An authorization header and allocation lines. Active Hold lines reference the exact issue movements reserved by FIFO. States are `active`, `captured`, `released`, and `expired`; every transition records time and actor. Expiration is 30 minutes.

### Grant configuration

Each consign program has explicit grant-rule headers. One header selects a trigger `product.product` and owns one or more entitlement child lines containing product, UoM, and quantity. Buying trigger quantity N grants N times each child quantity. Multiple child lines for one trigger are valid; ambiguity prevention applies between active rule headers in overlapping company/website scopes, not between children of one header. Global-company/global-website rules may not overlap a specific active rule for the same trigger. This replaces the ambiguous behavior that deposits every unrelated SO line and fails with multiple programs.

## Invariants

- Website users must be authenticated.
- Card `partner_id` must exactly equal order/POS customer `partner_id`.
- Card, operation, movement, Hold, order, and program must belong to the same company.
- A program may additionally restrict a website.
- Only exact `product.product` variants match.
- Quantities obey product UoM rounding; no `parseInt` or arbitrary decimal behavior.
- Cards and programs must be active consign records.
- Requests are aggregated before validation; duplicate source lines cannot bypass a balance check.
- Multi-card authorization/capture is one atomic operation. One failure rolls back all allocations.
- Lock order is global and deterministic for every command: idempotency advisory lock → cards/projections sorted by ID → Holds sorted by ID → issue movements sorted by ID → allocation rows. Authorization, capture, expiry, reversal, issue clawback, reconciliation, and deactivation all follow this order. Automatic card creation additionally uses a transaction-scoped advisory lock for the no-existing-row race.
- Available quantity is posted issue/reversal/adjustment-in minus redeem/adjustment-out and active Holds.
- Posted movements are immutable and non-deletable.
- Reversal quantity cannot exceed the still-unreversed quantity of the original redemption.
- All channel retries are idempotent.
- Entitlements never expire. Card allocation is oldest issue movement first, then ID.

## Website Cart and Checkout

Create `woow_loyalty_consign_website_sale`, depending on core, `website_sale`, and `website_sale_loyalty`.

The Cart lists only the authenticated partner's active cards, using a masked card number and aggregate per-product balances. Arbitrary card-code lookup is not offered.

`sale.order.consign.allocation` records customer intent: order, selected card, exact product, and requested quantity. It never stores a browser-supplied price or movement ID. Multiple cards may be used on one order, including multiple cards for the same product. A server-owned coverage projection then allocates that intent deterministically across exact non-reward SO lines (line sequence, then ID) and persists covered quantity, tax/price basis, generated reward line, and allocation version. This is required when the same product appears on lines with different taxes, discounts, or prices, and later drives paid-first refunds.

Every Cart mutation increments the allocation version, invalidates prior coverage/Holds, excludes generated reward lines from eligibility, and revalidates eligible quantities. If product quantity decreases, allocations are reduced to the new maximum and the user receives a visible warning. The server never silently changes selected cards. Any invalid allocation makes checkout authorization fail as a whole.

Pricing order:

1. apply pricelist and ordinary promotions;
2. calculate the eligible net quantity and value;
3. create server-generated reward lines that mirror the covered line's taxes and negate the promotion-adjusted net value.

Consignment is quantity entitlement. Historical issue price is audit-only.

At payment transaction creation, Odoo's locked order is revalidated and the engine creates a 30-minute Hold keyed by order plus allocation version and linked to the transaction. A newer transaction may not create a second active Hold for that version; superseded safe Holds are explicitly released. Provider state `authorized` is not actual collection for consignment: consign orders are prevented from confirming until the transaction reaches `done`. At callback, the adapter re-locks the SO, verifies the frozen allocation version and reward coverage, captures the Hold, and only then allows SO confirmation. A callback after expiry attempts a new atomic authorization; if unavailable, it blocks confirmation and starts a durable void/refund saga. Failure to refund produces an explicit `payment_exception` and a high-priority activity.

A zero-total order uses no fake payment transaction. The final website validation transaction authorizes, captures, and confirms the order atomically.

## Issuance

An SO confirmation does not grant entitlement.

- Website: issue after the payment transaction is `done`.
- POS: issue only after the paid order is durably persisted.
- General Sales: issue when the related customer invoice reaches fully paid.

Issuance is deduplicated by business quantity, not by callback source record. Each trigger sale line records cumulative paid trigger quantity already issued in its own UoM. Payment and invoice events normalize quantities to the sale-line UoM, lock the line, and issue only the positive delta. A later invoice event therefore cannot duplicate an earlier website payment event even though their technical sources differ; credit/reinvoice cycles preserve the cumulative ledger. Entitlement bought on an order cannot be used by that same order because it does not exist before payment.

## Refunds, Returns, and Cancellation

Use an explicit persisted saga; direct cancellation must not restore entitlement independently of money movement. A saga records source SO lines and captured coverage, the requested return quantities, exact cash amount/tax basis, child refund transaction, state, retry count, and resulting reversal operation.

1. Freeze paid-first allocation per original SO line/coverage, not merely per product.
2. Submit payment void/refund and link the asynchronous child transaction.
3. Only when the child refund transaction reaches terminal `done`, append reversals for the exact original redeem movements and any eligible issue clawback.
4. `pending`, `error`, and `cancel` remain durable states with idempotent retry; they do not restore entitlement.
5. If either side fails, leave an explicit exception requiring attention.

For a partial return of identical products, paid quantity is refunded first. Only quantities beyond the paid amount restore consignment entitlement. A covered unit restores one unit and refunds no cash. No-show remains consumed and creates no reversal.

Refunding a package that originally granted entitlement requires an `issue_reversal`. Automatic refund is allowed only for the quantity that remains unused and is not held. If any quantity required for a full refund has already been consumed or is under an active Hold, the system blocks the automatic full refund and creates a controlled manager exception; it never permits a negative entitlement or lets a customer retain consumed services plus a full refund.

Cards with active Holds cannot be directly deactivated. An emergency controlled action first cancels payment/Holds, then deactivates the card.

## Backend Adapter and Security

Create a dedicated consign manager group. Backend redemption remains available only to this group and requires reason and source. The wizard calls the engine and cannot write ledger records itself. Adjustments are append-only operations.

Portal users have read-only access only to their own consign cards, aggregate lines, and posted movements. New models use `check_company` relations and company record rules. Website allocation rows have no generic portal create/write ACL; authenticated controllers mutate them only after server-side order access validation. POS group references and ACLs live in the POS addon, so core installs without Point of Sale.

## POS Adapter

POS consignment requires an online connection. Scanning a card with no selected customer sets the card owner; scanning with a different selected customer is rejected.

POS obtains a Hold before payment. The Hold token and requested allocations are declared model fields serialized by `PosOrder.serialize()` and accepted as persisted `pos.order`/`pos.order.line` fields. Odoo 18 backend `sync_from_ui()` calls `_process_order(order, existing_order)`; the adapter persists lines first, re-derives authoritative coverage, and captures inside that durable order-processing transaction with the POS order UUID as idempotency source. Sync retries return the prior result. An expired Hold after terminal/cash payment becomes an explicit POS payment exception rather than silently syncing an uncovered order. `pos.config` has an explicit feature flag defaulting to false, and offline mode disables consignment controls.

## Data and Expiry Decisions

Entitlement never expires. Remove the nonexistent `date_expiry` references from email and PDF templates.

Current MujiMed cards, lines, and redemptions are fake test transactions and may be deleted before deployment after a final count and explicit confirmation. Preserve programs, rules, products, templates, and other configuration. No opening-balance migration is required.

## Delivery Sequence

1. Shared ledger engine and clean-install/security foundation.
2. Backend wizard and communications.
3. Website Cart, checkout, payment, and refund flow.
4. POS online authorization/capture.

Each phase is feature-gated and must pass clean install, upgrade, invariant, concurrency, security, and channel integration tests in an isolated database. Then run independent review, backup MujiMed, perform a disabled-feature smoke upgrade, and enable only after verification. Merge to `main` only after all gates pass.

## Rejected Approaches

- Channel-specific balance logic: duplicates invariants and race bugs.
- Reusing the current POS RPC from website: untrusted, non-idempotent, and non-atomic.
- Redeeming at Cart selection: abandoned carts consume entitlement.
- No Hold before provider payment: permits a charge after another channel consumes balance.
- Offline POS redemption: cannot coordinate shared balance.
- Monetary wallet semantics: consignment is exact-product quantity entitlement.
- Editing or deleting completed entries: destroys auditability.
- Card-level or line-level expiry: all entitlement is permanent.
- Migrating current MujiMed transactions: they are confirmed test data and will be reset with approval.
