# Architecture — Woow Odoo Member Center

This document describes the technical architecture and design decisions for the `woow_member_center` module.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Extend `CustomerPortal` | Reuse Odoo's existing portal infrastructure, breadcrumbs, and authentication |
| Query by `program_type` | Leverage Odoo's native loyalty program type field to categorize cards |
| SVG icons | Resolution-independent, lightweight, and customizable |
| Separate template files | One XML file per card type for maintainability |

---

## Portal Controller

### `MemberCenterPortal` (extends `CustomerPortal`)

**Hub page** (`/my/member-center`):
- Queries all loyalty card types by `program_type` (ewallet, loyalty, gift_card, coupons)
- Queries membership state from the partner record
- Renders a responsive card grid with quick-glance metrics

**Individual pages**:
- Each card type has list and detail routes
- Helper methods `_get_cards_by_type()` and `_get_single_card()` encapsulate common query patterns

### Portal Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/my/member-center` | `portal_member_center_hub` | Main hub with all card types |
| `/my/member-center/ewallet` | `portal_mc_ewallet` | eWallet list |
| `/my/member-center/ewallet/<id>` | `portal_mc_ewallet_detail` | eWallet detail |
| `/my/member-center/loyalty` | `portal_mc_loyalty_list` | Loyalty points list |
| `/my/member-center/loyalty/<id>` | `portal_mc_loyalty_detail` | Loyalty detail |
| `/my/member-center/gift-cards` | `portal_mc_gift_card_list` | Gift card list |
| `/my/member-center/gift-cards/<id>` | `portal_mc_gift_card_detail` | Gift card detail |
| `/my/member-center/coupons` | `portal_mc_coupon_list` | Coupon list |
| `/my/member-center/coupons/<id>` | `portal_mc_coupon_detail` | Coupon detail |
| `/my/member-center/membership` | `portal_mc_membership` | Membership status |

---

## Templates

### Portal Home Integration

The `portal_my_home_member_center` template inherits `portal.portal_my_home` to add a Member Center entry card on the portal home page.

### Hub Page

The `portal_member_center_hub` template renders a responsive grid of 5 card types:

1. **eWallet** — Shows total balance with currency formatting
2. **Loyalty** — Shows total accumulated points
3. **Gift Card** — Shows total gift card balance
4. **Coupon** — Shows count of available coupons
5. **Membership** — Shows current membership state with color-coded badge

### Breadcrumbs

The `portal_breadcrumb_member_center` template provides breadcrumb navigation for all sub-pages, using the `page_name` context variable to determine the current location.

---

## Security

- **ACLs** (`ir.model.access.csv`): Portal users get read-only access to loyalty cards
- **Record Rules** (`portal_security.xml`): Portal users can only see their own records
- All queries use `sudo()` for consistent access, with partner-scoped filters for data isolation
