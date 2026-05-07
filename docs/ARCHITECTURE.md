# Architecture — Woow Odoo Loyalty Card Enhance

This document describes the technical architecture, design decisions, and integration points for the `woow_loyalty_consign` and `woow_member_center` modules.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Extend `loyalty.card` instead of a new model | Reuse existing barcode generation, email templates, and portal infrastructure |
| Separate `consign.line` model | Track per-item quantities independently, unlike points-based loyalty where a single `points` field suffices |
| Trigger product mechanism | Same pattern as Odoo's gift card/ewallet — configure trigger products on the program, auto-create cards on sale order confirmation |
| POS via `_onCouponScan` patch | Reuse the existing coupon barcode scan flow (prefix `044`) — consign cards are tried first, then fall through to standard coupon handling |
| `_postPushOrderResolve` hook | Standard Odoo 18 pattern (used by `l10n_es_pos`) — confirms redemptions only after the order is successfully synced to the backend |
| XML ID product lookup | Use `env.ref('woow_loyalty_consign.consign_redemption_product')` instead of display name to avoid language-dependent breakage |

---

## Models

### `loyalty.program` (extended)

- Adds `consign` to the `program_type` selection field
- Adds `trigger_product_ids` (Many2many) for products that trigger card creation
- Controls visibility of consign-specific fields via `is_consign` computed boolean

### `loyalty.card` (extended)

- Adds `is_consign` (computed from `program_id.program_type`)
- Adds `consign_line_ids` (One2many → `loyalty.consign.line`)
- Adds `consign_redemption_ids` (One2many → `loyalty.consign.redemption`)
- Overrides `_compute_use_count` to show redemption count for consign cards
- Smart line consolidation: duplicate product lines from the same sale order are merged

### `loyalty.consign.line`

| Field | Type | Description |
|-------|------|-------------|
| `card_id` | Many2one | Parent consignment card |
| `product_id` | Many2one | The consigned product |
| `lot_id` | Many2one | Optional lot/serial tracking |
| `qty_total` | Float | Original quantity |
| `qty_remaining` | Float | Remaining quantity (decremented on redemption) |
| `source_order_line_id` | Many2one | Originating sale order line |
| `state` | Selection | `available` or `depleted` |

### `loyalty.consign.redemption`

| Field | Type | Description |
|-------|------|-------------|
| `card_id` | Many2one | Parent consignment card |
| `name` | Char | Sequence number (e.g., `REDEEM/00001`) |
| `pos_order_id` | Many2one | Linked POS order (if POS redemption) |
| `user_id` | Many2one | Staff who performed the redemption |
| `state` | Selection | `draft`, `done`, `cancelled` |
| `line_ids` | One2many | Redemption detail lines |

### `loyalty.consign.redemption.line`

| Field | Type | Description |
|-------|------|-------------|
| `redemption_id` | Many2one | Parent redemption document |
| `consign_line_id` | Many2one | Which consign line was redeemed |
| `qty_redeemed` | Float | Quantity redeemed in this line |

---

## Sale Order Integration

### Card Creation Flow

```
sale.order.action_confirm()
  └─ _action_consign_create_cards()
       ├─ Find all active consign programs
       ├─ Collect ALL trigger products across programs
       ├─ For each program:
       │    ├─ Match order lines to this program's trigger products
       │    ├─ Filter non-trigger lines (scoped against ALL programs' triggers)
       │    ├─ Create loyalty.card (is_consign=True)
       │    ├─ Create consign lines (consolidated by product)
       │    └─ Send email notification
       └─ Return
```

Key detail: When multiple consign programs exist, **all trigger products are collected upfront** to prevent non-trigger lines from being incorrectly assigned.

---

## POS Integration

### Component Architecture

```
ProductScreen (patched)
  ├─ _onCouponScan(code)
  │    ├─ RPC: pos.config.use_consign_card_code()
  │    ├─ If successful → open ConsignCardPopup
  │    ├─ If not_found → fall through to super (standard coupon)
  │    └─ If error → show notification
  │
  ├─ _openConsignCardPopup(cardData)
  │    └─ dialog.add(ConsignCardPopup, {...})
  │
  └─ _addConsignLinesToOrder(selection, productId)
       ├─ Store selection in order.uiState.consignRedemptions
       ├─ Look up redemption product by ID (XML ref)
       └─ Add $0 order lines with customer_note

PaymentScreen (patched)
  └─ _postPushOrderResolve(order, server_id)
       ├─ Call super first
       ├─ If order has consignRedemptions:
       │    └─ RPC: pos.order.confirm_consign_redemptions()
       └─ Clear uiState.consignRedemptions

ConsignCardPopup (OWL Component)
  ├─ Props: cardData, getPayload callback
  ├─ State: qty_redeemed per line (initialized to 0)
  ├─ Validation: 0 ≤ qty ≤ qty_remaining
  └─ Confirm: calls getPayload with selected lines
```

### RPC Endpoints

| Method | Model | Purpose |
|--------|-------|---------|
| `use_consign_card_code(code, partner_id)` | `pos.config` | Validate barcode, return card data + consign lines |
| `confirm_consign_redemptions(redemptions)` | `pos.order` | Create redemption records, decrement quantities |

---

## Portal Integration

### Customer Portal Routes

| Route | Controller | Description |
|-------|-----------|-------------|
| `/my/consign_cards` | `CustomerPortal` | List all consignment cards for the logged-in user |
| `/my/consign_cards/<int:card_id>` | `CustomerPortal` | Card detail with items and redemption history |
| `/my/member_center` | `MemberCenterPortal` | Unified hub with all card types |
| `/my/member_center/ewallet` | `MemberCenterPortal` | eWallet details |
| `/my/member_center/loyalty` | `MemberCenterPortal` | Loyalty points details |

### Security Model

- **Record Rules** (`ir.rule`): Portal users can only access records where `partner_id == user.partner_id`
- **ACLs**: Portal group gets read-only access to all consign models
- **No write access**: Portal users cannot modify any consignment data

---

## Email Notifications

A mail template (`consign_card_created_template`) is triggered when a consignment card is created:

- **Trigger**: `sale.order.action_confirm()` → card creation → `card.message_post_with_source()`
- **Content**: Card code (barcode), item list with quantities, link to portal page
- **Recipients**: The customer (`partner_id`) on the card

---

## PDF Report

- **Action**: `action_report_consign_card` (QWeb PDF report)
- **Template**: `report_consign_card_document`
- **Content**: Card header, barcode image, item table (product, qty total, qty remaining), redemption history table
