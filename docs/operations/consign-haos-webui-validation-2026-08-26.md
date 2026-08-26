# HAOS Consignment Full-Channel Validation — 2026-08-26

## Environment

- Web UI: `https://woowtech-odooo.woowtech.io/odoo`
- Odoo: 18.0-20260806
- Web-visible DB: `test_consign_all_20260825`
- Odoo container: `app_1b7b4ce7_odoo18ce`
- Persistent addons: `/data/addons`
- Clean concurrency DBs retained:
  - `test_consign_concurrency_20260826`
  - `test_consign_task6_20260826`
- Browser evidence: `/data/pi-agent/haos-validation-evidence/2026-08-26-consign-webui`
- HAOS logs: `/data/odoo/logs/consign-*.log`

`maindb` was preserved. No production deployment or push was performed.

## Final score

| Area | Score | Evidence |
|---|---:|---|
| Core ledger, authority, immutability | 98/100 | Full tests and Task4–6 real two-cursor probes pass |
| Backend manual redemption/adjustment | 96/100 | Browser adjustment and redemption produced immutable operations/movements |
| Sales paid-invoice issuance | 96/100 | Browser SO → invoice → payment issued exact grant; replay stayed idempotent |
| Website cart and checkout | 93/100 | Visible masked-card UI, Hold lifecycle, zero/nonzero checkout, exact transaction amount |
| POS online redemption | 94/100 | Draft preauthorization then same-order paid capture verified in Browser and DB |
| Refund/late-provider recovery | 78/100 | Core refund saga tests pass; provider-specific durable late-capture refund adapter remains |
| Operations/upgrade readiness | 91/100 | Persistent Web test DB, runbook, clean install, module upgrade and probes verified |

**Weighted overall: 93/100 — test-ready, not production-enable-ready.**

## Browser UI results

### Backend

- Card list and card form render authoritative projection: PASS.
- Manager-only adjustment `+2`: PASS.
- Manager-only redemption `2`: PASS.
- Redemption `RD-202608-0096` completed with authorize/capture operation links: PASS.
- Adjustment and redemption appended movements; resulting projection remained consistent: PASS.

### Sales and invoicing

Browser path:

`S00161 → Confirm → Draft Invoice → INV/2026/00002 → Post → Pay → Paid`

- No entitlement at SO confirmation: PASS.
- Paid invoice issued configured entitlement: PASS.
- Card balance increased by exactly five: PASS.
- Adapter replay did not append a duplicate movement: PASS.

### Website

- Portal owner login and shop product access: PASS.
- Visible `Use consignment balance` Cart UI: PASS.
- Masked card number (`••••49a8`), no full card code: PASS.
- Apply/remove allocation through visible UI: PASS.
- Expired Hold no longer blocks Cart mutation: PASS.
- Zero eligible quantity removes intent instead of writing invalid zero: PASS.
- Native base-line deletion leaves no orphan negative reward line: PASS.
- Nonzero Demo payment:
  - order `S00334`, amount `115.00`;
  - transaction `55`, amount `115.00`, state `done`;
  - Hold captured and card decremented exactly once: PASS.
- Fully covered zero-total order:
  - order `S00333`, total `0.00`, no transaction;
  - Hold captured before confirmation and card decremented exactly once: PASS.
- Stale payment amount mismatch is rejected before provider processing: PASS.

Historical test evidence retained: transaction `46` was created before the amount guard and demonstrated the original `230.00` vs final `0.00` mismatch. It must not be reused for acceptance testing.

### POS

- POS application boot and register opening: PASS.
- Customer selection and `Actions → Consignment`: PASS.
- Exact owner/card/product balance lookup: PASS.
- Declared persisted line intent uses actual entitlement product, card, and covered quantity: PASS.
- Draft pre-payment online authorization:
  - order `5`, state `draft`, consign state `authorized`, Hold `477`: PASS.
- Final payment uses the same order and Hold:
  - order `5`, state `paid`, consign state `captured`, capture operation `1866`: PASS.
- Obsolete post-payment `confirm_consign_redemptions` RPC removed: PASS.
- POS core order schema no longer replaced by backend-only custom fields: PASS.
- Persisted line quantity must equal covered quantity and price must remain zero: PASS.
- Fractional quantity input now uses UoM rounding instead of `parseInt`: PASS by static/model gate; dedicated fractional Browser tour remains desirable.

## Automated gates

### Static

- Python `compileall`: PASS.
- 28 XML/QWeb files parsed: PASS.
- Website/POS JavaScript syntax: PASS.
- `git diff --check`: PASS.

### Full HAOS module gate

Modules:

- `woow_loyalty_consign`
- `woow_loyalty_consign_website_sale`
- `woow_loyalty_consign_pos`
- `woow_consign_booking`

Result:

```text
157 tests
0 failures
0 errors
```

The two logged `loyalty.consign.movement rows are immutable` messages are expected negative SQL mutation tests; the final process exit code was zero.

### Concurrency

- Task4 issue/idempotency/projection repair: PASS.
- Task5 authorization/capacity/expiry SKIP LOCKED: PASS.
- Task6 capture/release/expiry/clawback lock-order races: PASS on isolated clean DB.

## Defects found and corrected during Browser iteration

1. Odoo 18 invoice product lines use `display_type='product'`; paid grants excluded them.
2. Paid-grant replay queried a nonexistent movement provenance field.
3. Expired active Hold blocked ordinary Cart changes.
4. Zero Cart quantity attempted to persist an invalid zero allocation.
5. Native base-line deletion orphaned a negative reward line.
6. Website had backend controllers but no visible Cart allocation UI.
7. Website transaction callback used transaction source instead of the Hold's sale-order source.
8. Website payment amount could be validated before coverage recomputation.
9. Fully covered zero-total checkout had no capture seam.
10. POS custom order loader replaced core frontend relations and crashed the POS loader.
11. POS frontend retained parallel untrusted state and called an obsolete post-payment RPC.
12. POS used a generic redemption product instead of persisted actual entitlement intent.
13. POS did not preauthorize online before opening PaymentScreen.
14. POS covered quantity was not equality-checked against persisted order-line quantity.
15. POS quantity UI truncated fractional UoMs.

## Residual production blockers

1. **Provider-success / late capture failure recovery:** implement and verify the durable payment-exception/refund adapter. The core refund saga exists, but provider-specific automatic refund/void orchestration is not complete.
2. **Fractional POS Browser tour:** add a dedicated Odoo HttpCase/tour for non-unit UoM rounding and offline UI behavior.
3. **Operational enablement:** keep Website/POS features disabled outside this test DB until monitoring, provider refund integration, rollback rehearsal, and staged approval are complete.
