# Atomic Consignment Redemption and eCommerce Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace mutable channel-specific consignment balances with one append-only, idempotent engine and add authenticated Cart/checkout redemption followed by a safe online POS adapter.

**Architecture:** `woow_loyalty_consign` owns an append-only movement ledger, operation journal, aggregate projections, and 30-minute Holds behind one private server interface. Backend, Sales/payment, website, and POS are thin adapters; they submit trusted source records and requested product quantities, while the engine owns validation, locking, FIFO allocation, and reversal.

**Tech Stack:** Odoo 18 Python ORM, PostgreSQL row/advisory locks and constraints, XML/QWeb, OWL/JavaScript, `website_sale`, `website_sale_loyalty`, `payment`, `account`, `pos_loyalty`, Odoo `TransactionCase`/`HttpCase`/HOOT tests.

---

## Execution Rules

- Use @odoo-18, @tdd, @diagnosing-bugs, and @verification-before-completion.
- Work in a dedicated worktree on `feature/atomic-consign-redemption-engine`, based on `origin/main`, while retaining `research/consign-redemption-audit` as evidence.
- Never test mutations in the MujiMed production database. Use a disposable database until the deployment task.
- Do not merge intentionally red audit tests into `main`. As each invariant is fixed, move it into a focused `standard` test class.
- Use Git author `Elmo <woowtech@designsmart.com.tw>`.
- Keep website and POS disabled by default until their phase gate passes.
- Before deleting MujiMed test transactions, show fresh counts and obtain explicit approval.

## Target Module Layout

```text
addons/woow_loyalty_consign/
  hooks.py
  models/
    loyalty_consign_engine.py
    loyalty_consign_operation.py
    loyalty_consign_movement.py
    loyalty_consign_hold.py
    loyalty_consign_refund_saga.py
    loyalty_consign_grant_rule.py
    loyalty_consign_grant_line.py
    loyalty_consign_line.py
    loyalty_card.py
    loyalty_program.py
    account_move.py
    sale_order.py
  security/
    consign_security.xml
    ir.model.access.csv
    portal_security.xml
  data/
    consign_hold_cron.xml
  tests/
    common.py
    test_consign_install.py
    test_consign_grants.py
    test_consign_issue.py
    test_consign_authorize.py
    test_consign_concurrency.py
    test_consign_reverse.py
    test_consign_security.py
    test_consign_issuance.py
    test_consign_rendering.py

addons/woow_loyalty_consign_website_sale/
  __init__.py
  __manifest__.py
  controllers/main.py
  models/sale_order.py
  models/sale_order_consign_coverage.py
  models/payment_transaction.py
  models/website.py
  security/ir.model.access.csv
  views/website_sale_templates.xml
  views/res_config_settings_views.xml
  static/src/js/consign_cart.js
  tests/test_consign_cart.py
  tests/test_consign_checkout.py
  tests/test_consign_refund.py

addons/woow_loyalty_consign_pos/
  models/pos_order.py
  models/pos_config.py
  security/ir.model.access.csv
  static/src/overrides/...
  tests/test_pos_consign_backend.py
  static/tests/tours/consign_redemption_tour.js
```

### Task 1: Establish the clean-install and regression baseline

**Files:**
- Modify: `addons/woow_loyalty_consign/__manifest__.py`
- Modify: `addons/woow_loyalty_consign/security/ir.model.access.csv`
- Create: `addons/woow_loyalty_consign_pos/security/ir.model.access.csv`
- Modify: `addons/woow_loyalty_consign_pos/__manifest__.py`
- Modify: `addons/woow_loyalty_consign/tests/__init__.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_install.py`

**Step 1: Write the failing clean-install test**

Create a standard test asserting the core registry contains no unresolved `point_of_sale` group reference and that core models initialize without POS installed. Keep the external process-level clean-install command as the authoritative check.

**Step 2: Run the current clean install and capture the expected failure**

```bash
dropdb --if-exists test_consign_clean
/usr/bin/odoo -d test_consign_clean \
  --init=woow_loyalty_consign --without-demo=all \
  --stop-after-init --no-http --log-level=test
```

Expected: FAIL with `No matching record found for external id 'point_of_sale.group_pos_user'`.

**Step 3: Remove leaked channel dependencies**

- Move the three POS ACL rows from the core CSV into the POS addon CSV.
- Remove `website_sale_loyalty` from core dependencies; the new website addon will own it.
- Keep `sale_loyalty`, `stock`, and `mail`; explicitly include `account` if not already guaranteed by the dependency graph.
- Bump core to `18.0.3.0.0` and POS to `18.0.2.0.0` only when their corresponding schema changes land.

**Step 4: Verify clean install**

Run the command from Step 2.

Expected: exit 0 and module state `installed` without `point_of_sale`.

**Step 5: Commit**

```bash
git add addons/woow_loyalty_consign addons/woow_loyalty_consign_pos
git commit -m "fix: isolate consign channel dependencies"
```

### Task 2: Replace ambiguous trigger behavior with explicit grant rules

**Files:**
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_grant_rule.py`
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_grant_line.py`
- Modify: `addons/woow_loyalty_consign/models/__init__.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_program.py`
- Modify: `addons/woow_loyalty_consign/views/loyalty_program_views.xml`
- Modify: `addons/woow_loyalty_consign/security/ir.model.access.csv`
- Create: `addons/woow_loyalty_consign/tests/test_consign_grants.py`

**Step 1: Write failing tests**

Cover:

```python
def test_one_trigger_can_grant_multiple_entitlement_products(self):
    # one rule header owns treatment x10 and aftercare x2 child lines

def test_trigger_quantity_multiplies_configured_grants(self):
    # trigger x2, configured treatment x10 => issue request treatment x20

def test_two_programs_do_not_claim_unrelated_order_lines(self):
    # each trigger resolves only its own configured grant lines

def test_duplicate_active_trigger_configuration_is_rejected(self):
    # same company/site/trigger cannot ambiguously grant twice
```

**Step 2: Run tests and verify red**

```bash
/usr/bin/odoo -d test_consign_engine -u woow_loyalty_consign \
  --test-enable --test-tags=/woow_loyalty_consign:TestConsignGrants \
  --stop-after-init --no-http
```

Expected: model/method missing failures.

**Step 3: Implement the grant model**

Use a rule header plus child lines:

```python
# loyalty.consign.grant.rule
program_id = fields.Many2one('loyalty.program', required=True, ondelete='cascade', index=True)
trigger_product_id = fields.Many2one('product.product', required=True, index=True)
grant_line_ids = fields.One2many('loyalty.consign.grant.line', 'rule_id', required=True)
company_id = fields.Many2one(related='program_id.company_id', store=True, index=True)

# loyalty.consign.grant.line
rule_id = fields.Many2one('loyalty.consign.grant.rule', required=True, ondelete='cascade')
entitlement_product_id = fields.Many2one('product.product', required=True, index=True)
product_uom_id = fields.Many2one('uom.uom', required=True)
quantity = fields.Float(required=True)
```

Validate positive quantity and UoM category compatibility. Multiple children under one header are valid. Prevent overlapping active **headers** for the same trigger across global/specific company and website scopes; test global-company and global-website overlap explicitly.

**Step 4: Replace the related-field trap**

Stop using `trigger_product_ids` as the source of truth for consignment issuance. The program form edits explicit grant rows. Preserve native loyalty rules only where native loyalty requires them; do not infer grants from unrelated SO lines.

**Step 5: Run tests and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "feat: configure explicit consign grants"
```

### Task 3: Add operation, movement, Hold, and projection schema

**Files:**
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_operation.py`
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_movement.py`
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_hold.py`
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_refund_saga.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_line.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_card.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_redemption.py`
- Modify: `addons/woow_loyalty_consign/views/loyalty_consign_line_views.xml`
- Modify: `addons/woow_loyalty_consign/views/loyalty_card_consign_views.xml`
- Modify: `addons/woow_consign_booking/models/loyalty_consign_line.py`
- Modify: `addons/woow_mc_consign/views/portal_templates.xml`
- Modify: `addons/woow_loyalty_consign/models/__init__.py`
- Create: `addons/woow_loyalty_consign/hooks.py`
- Modify: `addons/woow_loyalty_consign/__init__.py`
- Modify: `addons/woow_loyalty_consign/__manifest__.py`
- Create: `addons/woow_loyalty_consign/tests/common.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_issue.py`

**Step 1: Write schema/invariant tests**

Test unique operation keys, exact product/UoM projection uniqueness, positive movement quantity, required original links for reversals, and same-company/card/program constraints.

**Step 2: Implement the operation journal**

Use a unique SQL constraint on `(company_id, idempotency_key)` as a final guard, but acquire a transaction advisory lock derived from company/key before select-or-create. Store a canonical payload SHA-256. A repeated key with a different payload must raise `ValidationError`; simultaneous identical retries must block then return the same operation instead of surfacing `UniqueViolation`. Pure validation errors occur before journal insertion. Provider/payment failures that must survive are recorded by a non-raising outer saga/job so their rows are not rolled back with the request.

**Step 3: Implement aggregate lines**

Add `company_id`, `product_uom_id`, and read-only quantities:

```python
qty_issued
qty_redeemed
qty_reversed
qty_revoked
qty_on_hold
qty_available
```

Use `_sql_constraints` for `(card_id, product_id, product_uom_id)` uniqueness. Do not accept direct balance writes. For the first compatibility release, retain read-only computed aliases required by installed code/views: `qty_deposited`, `qty_remaining` (posted balance, before Holds), `unit_price`, amount/state fields, `redemption_line_ids`, and source display fields. Update `woow_consign_booking`, `woow_mc_consign`, core views, card totals, email, and report atomically to prefer `qty_available`; do not remove aliases until a later audited release.

**Step 4: Implement append-only movements**

Movement types:

```text
issue
redeem
redeem_reversal
issue_reversal
adjustment_in
adjustment_out
```

`issue_reversal` is required when refunding a package that granted entitlement. It may only consume still-unused, unheld issue quantity.

Override ORM `write()` and `unlink()` to reject all posted movement changes. Add a PostgreSQL trigger in `init()` that rejects direct SQL UPDATE/DELETE. Add an `uninstall_hook` that removes the trigger before module uninstall.

**Step 5: Implement Hold headers and FIFO allocation lines**

Hold states are `active`, `captured`, `released`, `expired`. Hold allocation lines reference aggregate line and exact issue movement.

**Step 6: Verify schema and commit**

```bash
/usr/bin/odoo -d test_consign_engine -u woow_loyalty_consign \
  --test-enable --test-tags=/woow_loyalty_consign:TestConsignSchema \
  --stop-after-init --no-http
git add addons/woow_loyalty_consign
git commit -m "feat: add append-only consign ledger schema"
```

### Task 4: Implement idempotent issue and projection reconciliation

**Files:**
- Create: `addons/woow_loyalty_consign/models/loyalty_consign_engine.py`
- Modify: `addons/woow_loyalty_consign/models/__init__.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_card.py`
- Modify: `addons/woow_loyalty_consign/tests/test_consign_issue.py`

**Step 1: Write failing interface tests**

Cover exact replay, payload mismatch, fresh-line immediate availability, multiple issue movements remaining independent, and automatic-card concurrency.

**Step 2: Implement a private server interface**

Odoo methods begin with `_` so they are not remotely callable:

```python
operation = self.env['loyalty.consign.engine']._issue(
    source=invoice,
    partner=partner,
    program=program,
    grants=[{'product': product, 'uom': uom, 'quantity': qty, 'source_line': line}],
    idempotency_key=key,
)
```

Normalize and hash payload before writing. On replay, return the existing completed operation.

**Step 3: Prevent duplicate automatic cards**

Take a transaction advisory lock derived from company/program/partner before searching or creating the automatic destination card. A `FOR UPDATE` query that returns no rows is insufficient.

**Step 4: Reconcile projection from ledger**

After each operation, recompute affected projections from posted movement and active Hold rows in SQL. Add `_assert_projection_consistent()` for tests and a manager repair action; never make incremental counters the sole truth.

**Step 5: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "feat: issue consign entitlement idempotently"
```

### Task 5: Implement atomic multi-card authorization and expiry

**Files:**
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_engine.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_hold.py`
- Create: `addons/woow_loyalty_consign/data/consign_hold_cron.xml`
- Modify: `addons/woow_loyalty_consign/__manifest__.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_authorize.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_concurrency.py`

**Step 1: Write failing tests**

Cover owner mismatch, company mismatch, inactive/non-consign card, exact variant, UoM rounding, duplicate request aggregation, FIFO, multiple cards for one product, all-or-nothing failure, and 30-minute expiry.

Add real two-cursor tests. Two operations each requesting 6 from 10 must leave exactly one successful allocation and an available balance of 4. Simultaneous identical idempotency keys return one operation. Add capture-vs-expiry, authorize-vs-issue-reversal, and deactivation-vs-release races. The interface must return a domain error after Odoo transaction retry rather than leak an unhandled `SerializationFailure` to the channel.

**Step 2: Implement normalized authorization**

- Aggregate requests by card/product/UoM.
- Follow one lock hierarchy everywhere: idempotency advisory lock → cards/projections sorted by ID → Holds sorted by ID → issue movements sorted by ID → allocation rows.
- Validate every request before inserting any Hold.
- Allocate oldest available issue movement, then ID.
- Use `float_compare` with UoM rounding.
- Set `expires_at = now + 30 minutes` server-side.

**Step 3: Add expiration cron**

Process bounded batches with `FOR UPDATE SKIP LOCKED`; transition only active expired Holds and reconcile projections. Re-running cron must be idempotent.

**Step 4: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "feat: authorize atomic consign holds"
```

### Task 6: Implement capture, release, redeem reversal, and issue clawback

**Files:**
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_engine.py`
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_hold.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_reverse.py`

**Step 1: Write failing lifecycle tests**

Cover:

- capture/release/expiry replay;
- capture of an expired Hold with atomic reacquisition;
- redeem reversal no greater than original unreversed quantity;
- covered return restores quantity but no money;
- no-show creates no reversal;
- package refund reverses only unused/unheld issue quantity;
- package full-refund request with consumed quantity returns a controlled exception;
- active Hold blocks issue clawback and card deactivation.

**Step 2: Implement lifecycle transitions**

Use operation keys such as:

```text
website-hold:<payment.transaction.reference>
website-capture:<payment.transaction.reference>
refund-redeem:<refund-source>:<sale-line>
refund-issue:<credit-note-line>
```

Never update movement rows. Capture inserts redeem movements and transitions the Hold. Reverse inserts `redeem_reversal`; package clawback inserts `issue_reversal` only after locking and proving the issue quantity is unused/unheld.

**Step 3: Implement controlled card deactivation**

Normal `active=False` is rejected when active Holds exist. Add a manager action that first runs the payment/Hold cancellation adapter, then deactivates after successful release.

**Step 4: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "feat: capture and reverse consign movements"
```

### Task 7: Rebuild backend redemption and manager adjustment around the engine

**Files:**
- Modify: `addons/woow_loyalty_consign/models/loyalty_consign_redemption.py`
- Modify: `addons/woow_loyalty_consign/wizard/consign_redeem_wizard.py`
- Modify: `addons/woow_loyalty_consign/wizard/consign_redeem_wizard_views.xml`
- Create: `addons/woow_loyalty_consign/wizard/consign_adjust_wizard.py`
- Create: `addons/woow_loyalty_consign/wizard/consign_adjust_wizard_views.xml`
- Create: `addons/woow_loyalty_consign/security/consign_security.xml`
- Rewrite: `addons/woow_loyalty_consign/security/ir.model.access.csv`
- Modify: `addons/woow_loyalty_consign/views/loyalty_consign_redemption_views.xml`
- Create: `addons/woow_loyalty_consign/tests/test_consign_backend.py`

**Step 1: Write failing role and replay tests**

Create users for Sales, POS-only, consign manager, and portal. Assert only the consign manager can execute manual issue/redeem/adjust/reverse operations. Add company record-rule tests and `check_company=True` on every relational field. Direct movement create/write/unlink remains forbidden for every UI group.

**Step 2: Make redemption documents an immutable business view**

Keep `loyalty.consign.redemption` for document/history compatibility, but link it to the engine operation and movements. A done document and its lines cannot be edited or deleted.

**Step 3: Rewrite the wizard**

Require `service_note`/reason and a stable client-generated submission UUID. Call `_authorize()` and `_capture()` in one transaction. A repeated click returns the same operation/document.

**Step 4: Implement explicit manager adjustment**

Add `_adjust(source, card, product, uom, quantity, reason, idempotency_key)`. Positive quantities create `adjustment_in`; negative quantities create `adjustment_out` only after the standard lock/balance checks. The wizard requires a reason and stable submission UUID, and cannot touch posted movements. Test positive, negative, insufficient-balance, wrong-company, replay, and non-manager denial.

**Step 5: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "refactor: route manual consign operations through ledger engine"
```

### Task 8: Move Sales issuance from confirmation to verified payment

**Files:**
- Rewrite: `addons/woow_loyalty_consign/models/sale_order.py`
- Create: `addons/woow_loyalty_consign/models/account_move.py`
- Modify: `addons/woow_loyalty_consign/models/__init__.py`
- Modify: `addons/woow_loyalty_consign/views/sale_order_views.xml`
- Create: `addons/woow_loyalty_consign/tests/test_consign_issuance.py`

**Step 1: Write failing payment-state tests**

Assert:

- `sale.order.action_confirm()` issues nothing;
- a draft/posted/unpaid invoice issues nothing;
- `account.move._invoice_paid_hook()` issues each paid invoice-line grant once;
- partial invoicing issues only the paid trigger quantity;
- reconciliation replay does not duplicate movements/cards;
- website payment event followed by invoice-paid event issues no duplicate quantity;
- invoice quantities in another compatible UoM normalize to sale-line UoM;
- credit/reinvoice cycles preserve cumulative issued quantity;
- one order with two configured programs issues both explicit grants;
- the same order cannot spend the entitlement it will later issue.

**Step 2: Remove `_action_create_consign_card()` from confirmation**

Delete order-line mutation based on `is_consigned` as the issuance authority. Preserve a read-only smart button by searching movement sources instead.

**Step 3: Implement the paid invoice adapter**

Override `account.move._invoice_paid_hook()`, call `super()`, filter customer invoices newly in `paid`, map invoice lines to sale lines and explicit grant rules, then call `_issue()` per deterministic source key.

Deduplicate by cumulative business quantity on each trigger sale line, not by technical event source. Lock the sale line, normalize paid invoice/payment quantity to its UoM, subtract trigger quantity already represented by issue movements, and issue only the positive delta. Thus a website payment event and a later invoice-paid event cannot duplicate entitlement despite different records/keys. Preserve this cumulative invariant through credit/reinvoice cycles.

**Step 4: Implement package-refund guard**

Before refunding a paid trigger line, calculate the issue movements it created. Permit automatic refund only when all quantity being refunded is unused and unheld. Link the asynchronous refund transaction to a persisted saga; only its terminal `done` callback appends `issue_reversal`. `pending/error/cancel` keep the entitlement and a retryable saga state. Otherwise raise a domain exception and create manager activity.

**Step 5: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "refactor: issue consign grants after payment"
```

### Task 9: Fix portal security and communication rendering

**Files:**
- Modify: `addons/woow_loyalty_consign/security/portal_security.xml`
- Modify: `addons/woow_loyalty_consign/data/mail_template_data.xml`
- Modify: `addons/woow_loyalty_consign/report/consign_card_report_templates.xml`
- Modify: `addons/woow_loyalty_consign/models/loyalty_card.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_security.py`
- Create: `addons/woow_loyalty_consign/tests/test_consign_rendering.py`

**Step 1: Promote audit cases into standard tests**

Portal user A must not search/read card, projection, redemption, or movement records belonging to partner B, including direct ID reads.

**Step 2: Add the missing card owner rule**

Add a portal rule on `loyalty.card` scoped to consign cards owned by `user.partner_id`. Ensure native loyalty portal rules combine safely; test the actual effective domain.

**Step 3: Remove expiry references**

Remove `date_expiry` from QWeb email/PDF. Render email HTML and report HTML in tests with multiple movement-backed products.

**Step 4: Verify and commit**

```bash
git add addons/woow_loyalty_consign
git commit -m "fix: secure and render consign portal records"
```

### Task 10: Create the website addon and Cart allocation model

**Files:**
- Create: `addons/woow_loyalty_consign_website_sale/__init__.py`
- Create: `addons/woow_loyalty_consign_website_sale/__manifest__.py`
- Create: `addons/woow_loyalty_consign_website_sale/models/__init__.py`
- Create: `addons/woow_loyalty_consign_website_sale/models/sale_order.py`
- Create: `addons/woow_loyalty_consign_website_sale/models/sale_order_consign_coverage.py`
- Create: `addons/woow_loyalty_consign_website_sale/models/website.py`
- Create: `addons/woow_loyalty_consign_website_sale/security/ir.model.access.csv`
- Create: `addons/woow_loyalty_consign_website_sale/views/res_config_settings_views.xml`
- Create: `addons/woow_loyalty_consign_website_sale/tests/__init__.py`
- Create: `addons/woow_loyalty_consign_website_sale/tests/test_consign_cart.py`

**Step 1: Write failing model tests**

Cover authenticated exact owner, feature disabled by default, multiple cards per order/product, sum not exceeding eligible Cart quantity, exact variant, company/website restrictions, automatic clamp after Cart quantity reduction, direct portal RPC denial, and deterministic allocation when the same product occurs on differently priced/taxed SO lines.

**Step 2: Create the addon and feature flag**

Depend on `woow_loyalty_consign`, `website_sale`, and `website_sale_loyalty`. Add a website/company setting `consign_redemption_enabled=False` by default.

**Step 3: Implement allocation intent**

Create `sale.order.consign.allocation` with order/card/product/UoM/requested quantity. Add a unique constraint per order/card/product/UoM. Browser input never sets price, owner, company, or movement. Do not grant portal users generic create/write ACL on allocation or coverage models; mutation is only through the authenticated controller after checking the current website order.

Add an order `consign_allocation_version` incremented on every relevant Cart/allocation mutation. Add a server-owned coverage model mapping intent to exact base SO line, covered quantity, tax/price fingerprint, generated reward line, and version. Add `sale.order._revalidate_consign_allocations()` and call it after `_cart_update()`. Return structured warnings for the controller/UI.

**Step 4: Verify and commit**

```bash
git add addons/woow_loyalty_consign_website_sale
git commit -m "feat: store website consign cart allocations"
```

### Task 11: Add authenticated Cart UI and server-priced reward lines

**Files:**
- Create: `addons/woow_loyalty_consign_website_sale/controllers/__init__.py`
- Create: `addons/woow_loyalty_consign_website_sale/controllers/main.py`
- Create: `addons/woow_loyalty_consign_website_sale/views/website_sale_templates.xml`
- Create: `addons/woow_loyalty_consign_website_sale/static/src/js/consign_cart.js`
- Modify: `addons/woow_loyalty_consign_website_sale/models/sale_order.py`
- Modify: `addons/woow_loyalty_consign_website_sale/tests/test_consign_cart.py`

**Step 1: Write failing controller/pricing tests**

Cover unauthenticated rejection, arbitrary card ID rejection, masked card code, own active cards only, partial quantity, multiple cards, promotion-first price, tax mirroring, no use of historical `unit_price`, duplicate product lines with different taxes/prices, tax-included pricing, native/global discount rewards, and repeated recomputation.

**Step 2: Add a thin authenticated JSON route**

Use `auth='user'` and obtain the order only through `request.website.sale_get_order()`. Re-resolve card and eligible quantities server-side. The route accepts card ID, product ID, and requested quantity only.

**Step 3: Implement reward-line recomputation**

Tag generated lines so native Cart operations can identify and exclude them from eligibility. Allocate each intent deterministically across eligible base lines by line sequence then ID. Persist one coverage row and one tagged negative line per tax/price basis. Recompute after ordinary loyalty/pricelist updates, based on promotion-adjusted covered base lines. Ensure discount line taxes and subtotal negate exactly the covered net value without trusting the browser; repeated recomputation must converge without duplicate reward lines.

**Step 4: Add the Cart UI**

Render aggregate balances, masked card number, UoM-aware controls, and visible clamp/error messages. Do not add a free-form card code endpoint.

**Step 5: Verify and commit**

```bash
git add addons/woow_loyalty_consign_website_sale
git commit -m "feat: apply consign quantities in website cart"
```

### Task 12: Integrate Holds with payment creation, callback, and zero-total confirmation

**Files:**
- Create: `addons/woow_loyalty_consign_website_sale/models/payment_transaction.py`
- Modify: `addons/woow_loyalty_consign_website_sale/models/__init__.py`
- Modify: `addons/woow_loyalty_consign_website_sale/controllers/main.py`
- Create: `addons/woow_loyalty_consign_website_sale/tests/test_consign_checkout.py`

**Step 1: Write failing checkout tests**

Cover active Hold creation, all-or-nothing multi-card failure, provider `done` capture, provider `authorized` non-confirmation, provider failure release, repeated callback, two transactions for one order/version, Cart mutation after transaction creation, expired Hold reacquisition, unavailable late callback, and zero-total rollback.

**Step 2: Authorize at Odoo's locked payment seam**

Extend Odoo 18 `website_sale.controllers.payment.PaymentPortal._validate_transaction_for_order()`. Reject the public website user for any order with consign allocations even if an access token is valid, then call `super()` so native loyalty has refreshed rewards. Authorize using the locked SO, its current allocation version, coverage hash, and newly created transaction. Enforce one active Hold per order/allocation version; reject or safely release a superseded transaction Hold. Authorization occurs before `_send_payment_request()` for token flows.

**Step 3: Capture before SO confirmation**

Extend Odoo 18 `payment.transaction._check_amount_and_confirm_order()`, the exact sale confirmation seam called for both `authorized` and `done`. Re-lock the SO and coverage. For consign orders in `authorized`, return without confirmation because funds are not collected. For `done`, require the same allocation version/coverage hash, capture or reacquire first, then delegate to confirmation. Replays return the existing capture operation. A second transaction cannot capture an already consumed order version.

**Step 4: Handle provider failure/cancel**

Release active Holds idempotently when transaction state becomes `cancel` or `error`.

**Step 5: Handle zero-total orders**

Extend the `sale.order._validate_order()` seam used by `/shop/payment/validate`. When total is zero and allocations exist, authorize and capture before `super()` confirms; any exception rolls back all three actions.

**Step 6: Implement late-callback exception state**

If reacquisition fails, do not call SO confirmation. Create and commit a durable exception saga/job without re-raising away its own rows, request provider void/refund through supported Odoo payment methods, and link the child transaction. Persist `pending/error/cancel/done`, retries, and activity; only terminal `done` closes the saga.

**Step 7: Verify and commit**

```bash
git add addons/woow_loyalty_consign_website_sale
git commit -m "feat: capture consign holds during checkout"
```

### Task 13: Implement controlled cancellation, refunds, and paid-first returns

**Files:**
- Create: `addons/woow_loyalty_consign_website_sale/models/consign_refund.py`
- Modify: `addons/woow_loyalty_consign_website_sale/models/payment_transaction.py`
- Modify: `addons/woow_loyalty_consign_website_sale/models/sale_order.py`
- Create: `addons/woow_loyalty_consign_website_sale/views/consign_refund_views.xml`
- Create: `addons/woow_loyalty_consign_website_sale/tests/test_consign_refund.py`

**Step 1: Write failing saga tests**

Test full cancellation, partial identical-product return across different SO line taxes/prices, prior partial returns, paid-first allocation, covered-unit no-cash behavior, asynchronous provider refund `pending/done/error/cancel`, replay, no-show non-reversal, and package grant refund guard.

**Step 2: Add a controlled refund operation**

Persist a saga header linked to SO, source lines, child refund transaction, state, retry count, requested cash amount/tax basis, coverage version, and reversal operation. Calculate paid-first quantities per original SO line and captured coverage—not merely per product:

```text
line_return_qty
paid_return_qty = min(line_return_qty, still_refundable_paid_qty_on_line)
consign_return_qty = line_return_qty - paid_return_qty
```

Submit only the exact line/tax cash amount for `paid_return_qty`. Do not reverse at request time. When the linked child refund transaction reaches terminal `done`, idempotently reverse the exact redeem movements for `consign_return_qty`; `pending/error/cancel` retain entitlement and durable saga state. Apply the same callback rule to package `issue_reversal`.

**Step 3: Guard direct cancellation**

If a confirmed order has captured consign operations, direct cancellation must route through or require completion of the controlled refund saga. Never restore entitlement from `action_cancel()` alone.

**Step 4: Verify and commit**

```bash
git add addons/woow_loyalty_consign_website_sale
git commit -m "feat: reverse consign redemption after refunds"
```

### Task 14: Complete website integration and browser regression tests

**Files:**
- Modify: `addons/woow_loyalty_consign_website_sale/tests/test_consign_cart.py`
- Modify: `addons/woow_loyalty_consign_website_sale/tests/test_consign_checkout.py`
- Create: `addons/woow_loyalty_consign_website_sale/static/tests/tours/consign_checkout_tour.js`
- Create: `addons/woow_loyalty_consign_website_sale/tests/test_consign_tour.py`
- Modify: `addons/woow_loyalty_consign_website_sale/__manifest__.py`

**Step 1: Add an end-to-end tour**

Create a tagged Python `HttpCase` whose test calls `self.start_tour(...)`. Log in, add eligible products, select two cards, partially cover one product, alter Cart quantity, observe clamp warning, enter checkout, and complete Demo payment. Verify reward lines, SO state, captured operation, movement quantities, and remaining card balances.

**Step 2: Add adversarial HTTP tests**

Attempt another partner's card ID, another company, inactive card, changed Cart after payment page load, duplicate transaction request, and forged price/quantity payload.

**Step 3: Run the complete website phase gate**

```bash
/usr/bin/odoo -d test_consign_website \
  --init=woow_loyalty_consign,woow_loyalty_consign_website_sale \
  --test-enable \
  --test-tags=/woow_loyalty_consign,/woow_loyalty_consign_website_sale \
  --stop-after-init --http-port=18069
```

Expected: zero failures/errors; website feature remains disabled by default.

**Step 4: Commit**

```bash
git add addons/woow_loyalty_consign_website_sale
git commit -m "test: cover website consign checkout end to end"
```

### Task 15: Rebuild the POS backend as an online adapter

**Files:**
- Rewrite: `addons/woow_loyalty_consign_pos/models/pos_order.py`
- Modify: `addons/woow_loyalty_consign_pos/models/pos_config.py`
- Modify: `addons/woow_loyalty_consign_pos/models/pos_order_line.py`
- Modify: `addons/woow_loyalty_consign_pos/models/__init__.py`
- Modify: `addons/woow_loyalty_consign_pos/security/ir.model.access.csv`
- Replace: `addons/woow_loyalty_consign_pos/tests/test_pos_redemption_audit.py`
- Create: `addons/woow_loyalty_consign_pos/tests/test_pos_consign_backend.py`

**Step 1: Promote POS audit invariants to standard tests**

Cover auto-owner lookup, wrong owner, inactive card, feature flag disabled by default, persisted order lines authoritative, POS-only cashier, fractional UoM, Hold token ownership, UUID callback replay, expired Hold after terminal payment, and rollback when post-persistence capture fails.

**Step 2: Replace `confirm_consign_redemptions()`**

Do not create redemption documents from frontend line IDs. Add `pos.config.enable_consign_redemption=False`; declared fields on `pos.order` for Hold token/allocation hash/state; and declared `pos.order.line` fields for selected card and covered quantity. Add server methods that:

- authorize from card/product/quantity intent while online;
- return an opaque Hold token;
- serialize declared fields through Odoo 18 `PosOrder.serialize()`;
- override Odoo 18 `pos.order._process_order(order, existing_order)`, call `super()` so lines are persisted, then re-derive coverage from those records and capture before returning;
- allow capture failure to propagate so `sync_from_ui()` rolls back order writes rather than swallowing it inside `action_pos_order_paid()`;
- verify partner/card/company/config and capture using POS order UUID.

If a terminal/cash payment reaches backend after Hold expiry, atomically reacquire before capture; inability to reacquire creates the explicit payment-exception/refund path and never silently persists an uncovered paid order. POS users receive execute access only through model methods; they receive no create/write/unlink ACL on movement models.

**Step 3: Verify and commit**

```bash
git add addons/woow_loyalty_consign_pos
git commit -m "refactor: authorize POS consign redemption online"
```

### Task 16: Rebuild POS frontend state and offline behavior

**Files:**
- Rewrite: `addons/woow_loyalty_consign_pos/static/src/overrides/components/consign_card_popup/consign_card_popup.js`
- Rewrite: `addons/woow_loyalty_consign_pos/static/src/overrides/components/payment_screen/payment_screen.js`
- Modify: `addons/woow_loyalty_consign_pos/static/src/overrides/components/product_screen/product_screen.js`
- Rewrite: `addons/woow_loyalty_consign_pos/static/src/overrides/models/pos_order.js`
- Create: `addons/woow_loyalty_consign_pos/static/tests/tours/consign_redemption_tour.js`
- Create: `addons/woow_loyalty_consign_pos/tests/test_pos_consign_tour.py`

**Step 1: Write failing HOOT/tour cases**

Cover offline disable, scan without customer auto-select, different customer rejection, UoM-aware quantity, deleted/changed order lines, authorization failure before payment, and sync retry.

**Step 2: Remove parallel untrusted state**

Store requested allocation in declared PosOrder/PosOrderLine fields tied to real POS lines. Patch `PosOrder.serialize()` only for required conversion while preserving `super.serialize()`. Do not retain stale `consignRedemptions` after line/customer changes. Eliminate `parseInt`; apply UoM rounding from loaded product data.

**Step 3: Require online authorization before payment validation**

Block payment completion until a valid Hold token exists. If network is offline or authorization fails, leave the order unpaid and show an actionable error.

**Step 4: Run the POS phase gate and commit**

Add a Python `HttpCase` runner that calls `self.start_tour(...)`. Run model tests separately with `--no-http`, then run the tour gate with HTTP enabled:

```bash
/usr/bin/odoo -d test_consign_pos \
  --update=woow_loyalty_consign_pos \
  --test-enable --test-tags=/woow_loyalty_consign_pos:TestPosConsignBackend \
  --stop-after-init --no-http
/usr/bin/odoo -d test_consign_pos \
  --update=woow_loyalty_consign_pos \
  --test-enable --test-tags=/woow_loyalty_consign_pos:TestPosConsignTour \
  --stop-after-init --http-port=18070
git add addons/woow_loyalty_consign_pos
git commit -m "feat: make POS consign redemption online and idempotent"
```

### Task 17: Run whole-repository verification and independent review

**Files:**
- Modify: `docs/research/2026-08-24-consign-redemption-audit.md`
- Create: `docs/operations/consign-redemption-runbook.md`

**Step 1: Run static verification**

```bash
python3 -m compileall -q addons/woow_loyalty_consign \
  addons/woow_loyalty_consign_website_sale addons/woow_loyalty_consign_pos
python3 - <<'PY'
from pathlib import Path
from lxml import etree
for path in Path('addons').glob('woow_loyalty_consign*/**/*.xml'):
    etree.parse(str(path))
print('XML: PASS')
PY
git diff --check
```

**Step 2: Run clean-install and upgrade matrices**

Test:

1. core only;
2. core + website;
3. core + POS;
4. all three;
5. upgrade from current `main` schema with zero legacy transactions;
6. upgrade with installed dependents `woow_consign_booking` and `woow_mc_consign`, validating every Python/XML/QWeb consumer of compatibility fields;
7. install with demo disabled.

Expected: zero failures/errors and no POS/website external-ID leak.

**Step 3: Run security and concurrency probes**

Include direct RPC/record access, two-cursor overspend, duplicate callbacks, multi-card deadlock ordering, cron `SKIP LOCKED`, and SQL movement mutation rejection.

**Step 4: Obtain two-axis independent review**

Review Standards against repository/Odoo 18 conventions and Spec against the accepted design. Resolve all P0/P1 findings before deployment.

**Step 5: Write the runbook**

Document feature flags, Hold expiry monitoring, payment exceptions, manual reversals, emergency card deactivation, projection reconciliation, and rollback steps.

**Step 6: Commit**

```bash
git add docs addons
git commit -m "docs: add consign redemption operations runbook"
```

### Task 18: MujiMed reset, disabled-feature upgrade, and staged enablement

**Files:**
- No source changes unless verification finds a defect.
- Operational artifacts go outside Git and must contain no credentials.

**Step 1: Preflight without mutation**

Record:

- module versions;
- card/line/redemption/movement/Hold counts;
- deposited/redeemed quantities by card and product;
- website/POS feature flags;
- deployment readiness and `/web/health`.

Expected legacy transaction baseline: 3 cards, 7 lines, deposited 40, redeemed 0, zero redemptions; do not rely on this stale count—show fresh values.

**Step 2: Obtain explicit deletion approval**

State exactly that the operation will delete only consign cards, consign lines, and redemption transactions. Programs, grant configuration, rules, products, templates, partners, SOs, and other loyalty data remain.

**Step 3: Back up**

Create a timestamped PostgreSQL dump and addon archive. Verify the dump is non-empty and record checksums.

**Step 4: Reset approved test transactions**

Use an Odoo shell operation in dependency order and a database savepoint. Re-read counts before commit. Abort if counts or model scope differ from the approved preflight.

**Step 5: Upgrade with all new features disabled**

Upgrade core first, run standard tests in a clone, verify projection/movement/Hold tables, render email/report, and check HTTP health. Then install website addon disabled. POS stays uninstalled until its own phase.

**Step 6: Enable website in a controlled window**

Use a dedicated test customer/card created after upgrade. Complete paid, partially covered, zero-total, failed-payment, expiry, and refund scenarios. Remove only the new test transaction data through controlled reversal/reset procedures.

**Step 7: Enable POS only after website/core soak**

Install POS dependencies/addon, enable one test POS config, execute online scan/Hold/payment/sync/retry tests, then expand rollout.

**Step 8: Final verification**

```text
module versions = expected
legacy transaction reset = expected
active Holds = 0 after test cleanup
movement projection reconciliation = exact
payment exceptions = 0
/web/health = 200
/web/login = 200
deployment ready/available/updated = 1/1/1
```

**Step 9: Merge only after evidence and review**

Fast-forward or merge the reviewed feature branch to `main`, push, and verify local/origin hashes match with a clean worktree.

## Final Acceptance Matrix

- Core installs without website or POS.
- No channel can over-redeem through duplicate lines, concurrency, or callback replay.
- Every issue/redeem/reversal/clawback has immutable source provenance.
- Manual redemption requires consign manager and reason.
- Portal and website users can see/use only exact-owner cards.
- Cart supports multi-card partial quantity at promotion-adjusted net value.
- Checkout Hold is 30 minutes, all-or-nothing, and payment-aware.
- Zero-total orders atomically redeem and confirm.
- Late callbacks refund/void or enter explicit exception; they never silently confirm.
- Paid-first returns and quantity-only consign restoration are tested.
- Package refunds cannot claw back consumed/held rights automatically.
- No-show never restores entitlement.
- Sales issues only after verified payment from explicit grant rules.
- POS is online-only, server-authoritative, and UUID-idempotent.
- MujiMed remains feature-disabled until each phase gate is green.
