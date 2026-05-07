<p align="center">
  <img src="docs/images/icon_member_center.png" alt="Woow Loyalty Card Enhance" width="120">
</p>

<h1 align="center">Woow Odoo Loyalty Card Enhance</h1>

<p align="center">
  <a href="https://www.odoo.com"><img src="https://img.shields.io/badge/Odoo-18.0-714B67?logo=odoo&logoColor=white" alt="Odoo 18.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-LGPL--3-blue.svg" alt="License: LGPL-3"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12"></a>
  <a href="https://www.woow.tw"><img src="https://img.shields.io/badge/Author-WoowTech-FF6B35" alt="Author: WoowTech"></a>
</p>

<p align="center">
  <b>Consignment Card & Member Center — extending Odoo 18 Loyalty for wine cellars, med-spa prepaid treatments, and more.</b>
</p>

<p align="center">
  <a href="README_zh-TW.md">繁體中文</a> · English
</p>

---

## Table of Contents

[Overview](#overview) · [Modules](#modules) · [Architecture](#architecture) · [Features](#features) · [Screenshots](#screenshots) · [Installation](#installation) · [Configuration](#configuration) · [Usage](#usage) · [Technical Details](#technical-details) · [Roadmap](#roadmap) · [Contributing](#contributing) · [License](#license) · [Author](#author)

---

## Overview

| | |
|---|---|
| **Problem** | Odoo 18's built-in loyalty module supports points, eWallets, gift cards, and coupons — but lacks a mechanism for customers to **pre-purchase physical items** (wine bottles, spa sessions) and **redeem them later** one by one. |
| **Solution** | This add-on suite introduces a **Consignment Card** (`consign`) program type that tracks per-item quantities, supports POS barcode scanning for in-store redemption, and provides a unified **Member Center** portal for customers to check all their loyalty benefits. |

### Key Capabilities

- **Consignment Card** — Pre-purchase items, store them at the merchant, redeem individually via POS barcode scan or backend wizard
- **Automatic Card Creation** — Confirmed sale orders with trigger products automatically generate consignment cards
- **POS Integration** — Staff scans the card barcode → selects items → $0 redemption lines added to the order
- **Customer Portal** — Self-service card balance and redemption history
- **Member Center Hub** — Unified portal page aggregating eWallet, loyalty points, gift cards, coupons, membership, and consignment cards

---

## Modules

| Module | Summary | Dependencies |
|--------|---------|-------------|
| **`woow_loyalty_consign`** | Consignment card engine: program type, card model, consign lines, redemption records, POS integration, portal page, PDF report | `loyalty`, `sale_loyalty`, `pos_loyalty`, `stock`, `portal`, `mail` |
| **`woow_member_center`** | Unified member center portal hub aggregating all loyalty card types into a single responsive page | `portal`, `loyalty`, `membership`, `woow_loyalty_consign` |

---

## Architecture

### System Architecture

```mermaid
flowchart TB
    subgraph Backend["🖥️ Backend (Python)"]
        LP["loyalty.program\n(type=consign)"]
        LC["loyalty.card\n(is_consign=True)"]
        CL["loyalty.consign.line\n(item + qty)"]
        CR["loyalty.consign.redemption"]
        CRL["loyalty.consign.redemption.line"]
        SO["sale.order"]
        TP["Trigger Products"]
    end

    subgraph POS["📱 POS (OWL JS)"]
        PS["ProductScreen\nbarcode scan"]
        PP["ConsignCardPopup\nitem selection"]
        PAY["PaymentScreen\npost-push hook"]
    end

    subgraph Portal["🌐 Customer Portal"]
        MC["Member Center Hub"]
        CP["Consign Card Page"]
        EW["eWallet / Loyalty / Gift Card"]
    end

    SO -->|"confirm → auto-create"| LC
    TP -.->|"trigger"| SO
    LP -->|"has many"| LC
    LC -->|"contains"| CL
    LC -->|"tracks"| CR
    CR -->|"details"| CRL
    CRL -.->|"redeems"| CL

    PS -->|"scan 044*"| PP
    PP -->|"$0 lines"| PAY
    PAY -->|"confirm_consign_redemptions()"| CR

    MC --> CP
    MC --> EW
    CP -.->|"read-only"| LC
```

### Data Model

```mermaid
erDiagram
    loyalty_program {
        string program_type
        many2many trigger_product_ids
        boolean is_consign
    }
    loyalty_card {
        string code
        boolean is_consign
        many2one program_id
        many2one partner_id
    }
    loyalty_consign_line {
        many2one card_id
        many2one product_id
        float qty_total
        float qty_remaining
        string state
    }
    loyalty_consign_redemption {
        many2one card_id
        string name
        many2one pos_order_id
        string state
    }
    loyalty_consign_redemption_line {
        many2one redemption_id
        many2one consign_line_id
        float qty_redeemed
    }

    loyalty_program ||--o{ loyalty_card : "has"
    loyalty_card ||--o{ loyalty_consign_line : "contains"
    loyalty_card ||--o{ loyalty_consign_redemption : "tracks"
    loyalty_consign_redemption ||--o{ loyalty_consign_redemption_line : "details"
    loyalty_consign_line ||--o{ loyalty_consign_redemption_line : "redeems"
```

### POS Redemption Flow

```mermaid
sequenceDiagram
    participant Staff as POS Staff
    participant PS as ProductScreen
    participant API as pos.config RPC
    participant Popup as ConsignCardPopup
    participant Pay as PaymentScreen
    participant DB as Backend DB

    Staff->>PS: Scan barcode (044*)
    PS->>API: use_consign_card_code()
    API-->>PS: card data + consign lines
    PS->>Popup: Open with available items
    Staff->>Popup: Select items & quantities
    Popup->>PS: Add $0 redemption lines to order
    Staff->>Pay: Complete payment
    Pay->>DB: confirm_consign_redemptions()
    DB-->>Pay: Success ✓
```

---

## Features

### Consignment Card (`woow_loyalty_consign`)

- **New Program Type** — `consign` added to `loyalty.program` selection alongside existing types (loyalty, ewallet, gift_card, etc.)
- **Trigger Product Mechanism** — Link products to a consign program; when a sale order containing these products is confirmed, a consignment card is automatically created with per-item quantity lines
- **Consign Lines** — Track individual items with `qty_total`, `qty_remaining`, product reference, and state (`available` / `depleted`)
- **Backend Redemption Wizard** — Staff can redeem items via a wizard directly from the card form view
- **POS Barcode Integration** — Scan the card barcode at POS → popup shows available items → select quantities → $0 lines added to the POS order → redemption confirmed on payment
- **Redemption Records** — Full audit trail with sequence-numbered documents, linked to POS orders or manual operations
- **Customer Portal** — Card holders can view their cards, remaining items, and redemption history through the Odoo portal
- **PDF Report** — Printable card report with barcode, item list, and remaining quantities
- **Email Notification** — Automatic email sent to the customer when a consignment card is created

### Member Center (`woow_member_center`)

- **Unified Portal Hub** — Single responsive page aggregating all loyalty card types
- **Supported Card Types** — eWallet balance, loyalty points, gift cards, coupons, membership status, consignment cards
- **Mobile-First Design** — Responsive layout with SVG icons, optimized for both desktop and mobile
- **Deep Links** — Each card type links to its detailed page for full information

---

## Screenshots

### Backend Views

<p align="center">
  <img src="docs/images/backend-program-type-consign.png" alt="Consign Program Type" width="700"><br>
  <em>Consignment type in loyalty program type dropdown</em>
</p>

<p align="center">
  <img src="docs/images/backend-consign-line-form.png" alt="Consign Line Form" width="700"><br>
  <em>Consignment line form — tracking item quantities</em>
</p>

<p align="center">
  <img src="docs/images/backend-menu-structure.png" alt="Backend Menu" width="700"><br>
  <em>Backend menu structure — Consignment Cards under Sales</em>
</p>

<p align="center">
  <img src="docs/images/backend-program-type-giftcard.png" alt="Gift Card Program Type" width="700"><br>
  <em>Gift Card / eWallet program type dropdown (for comparison)</em>
</p>

### Portal / Member Center

<p align="center">
  <img src="docs/images/portal-consign-card-detail.png" alt="Portal Consign Card Detail" width="700"><br>
  <em>Customer portal — consignment card detail with items and redemption history</em>
</p>

<p align="center">
  <img src="docs/images/member-center-hub-mobile.png" alt="Member Center Hub" width="350"><br>
  <em>Member center hub (mobile) — all card types at a glance</em>
</p>

<p align="center">
  <img src="docs/images/portal-home-mobile.png" alt="Portal Home Mobile" width="350"><br>
  <em>Portal home (mobile) with member center entry point</em>
</p>

<p align="center">
  <img src="docs/images/member-center-ewallet-detail.png" alt="eWallet Detail" width="350"><br>
  <em>Member center — eWallet balance detail</em>
</p>

<p align="center">
  <img src="docs/images/member-center-loyalty-detail.png" alt="Loyalty Detail" width="350"><br>
  <em>Member center — loyalty points detail</em>
</p>

<p align="center">
  <img src="docs/images/member-center-membership.png" alt="Membership" width="350"><br>
  <em>Member center — membership status</em>
</p>

---

## Installation

1. Clone this repository into your Odoo addons directory:
   ```bash
   cd /path/to/odoo/addons
   git clone https://github.com/WOOWTECH/Woow_odoo_loyalty_card_enhance.git
   ```

2. Add the repository path to your Odoo configuration:
   ```ini
   [options]
   addons_path = /path/to/odoo/addons,/path/to/Woow_odoo_loyalty_card_enhance
   ```

3. Restart Odoo and update the module list:
   ```bash
   odoo -u base --stop-after-init
   ```

4. Install modules from the Odoo Apps menu:
   - Search for **"寄品卡"** or **"Consignment Card"** → Install `woow_loyalty_consign`
   - Search for **"會員中心"** or **"Member Center"** → Install `woow_member_center`

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Odoo | 18.0 (Community or Enterprise) |
| Python | 3.12+ |
| Required Odoo modules | `loyalty`, `sale_loyalty`, `pos_loyalty`, `stock`, `portal`, `mail`, `membership` |

---

## Configuration

### 1. Create a Consign Program

1. Navigate to **Sales → Products → Loyalty Cards & Gift Cards**
2. Click **New** and select program type **Consignment Card (寄品卡)**
3. In the **Trigger Products** tab, add the products that should generate consignment cards when sold
4. Configure email template and card validity as needed

### 2. POS Setup

1. The POS barcode rules (prefix `044`) are automatically configured
2. Ensure the **Consignment Redemption Product** (auto-created data record) is available in your POS product list
3. POS users need the **Point of Sale / User** group (ACLs are pre-configured)

### 3. Portal Access

- Portal users automatically see their consignment cards under **My Account → Consignment Cards**
- Install `woow_member_center` for the unified member center hub

---

## Usage

### Create a Consign Program

1. Go to **Sales → Products → Loyalty Cards & Gift Cards**
2. Create a new program with type **Consignment Card**
3. Add trigger products (e.g., "Wine Case — 6 bottles")

### Sale Order → Auto Card Creation

1. Create a sale order with trigger products
2. Confirm the sale order
3. A consignment card is automatically created with individual item lines
4. Customer receives an email notification with their card details

### POS Barcode Redemption

1. At the POS, scan the customer's consignment card barcode
2. A popup displays available items with remaining quantities
3. Select items and quantities to redeem
4. Confirm — $0 redemption lines are added to the order
5. Complete payment — redemption is recorded in the backend

### Customer Portal

1. Customer logs into the Odoo portal
2. Navigates to **My Account → Consignment Cards** (or **Member Center**)
3. Views card balance, item list, and redemption history

---

## Technical Details

### Module Dependencies

```mermaid
graph LR
    WLC[woow_loyalty_consign] --> loyalty
    WLC --> sale_loyalty
    WLC --> pos_loyalty
    WLC --> stock
    WLC --> portal
    WLC --> mail
    WMC[woow_member_center] --> portal
    WMC --> loyalty
    WMC --> membership
    WMC --> WLC
```

### File Structure

```
Woow_odoo_loyalty_card_enhance/
├── woow_loyalty_consign/
│   ├── __manifest__.py
│   ├── __init__.py
│   ├── models/
│   │   ├── loyalty_program.py          # consign program type
│   │   ├── loyalty_card.py             # card extension
│   │   ├── loyalty_consign_line.py     # per-item tracking
│   │   ├── loyalty_consign_redemption.py  # redemption documents
│   │   ├── sale_order.py               # auto card creation
│   │   ├── pos_config.py              # POS barcode RPC
│   │   ├── pos_order.py              # POS redemption confirmation
│   │   └── pos_order_line.py         # POS line extension
│   ├── wizard/
│   │   └── consign_redeem_wizard.py   # backend redemption wizard
│   ├── controllers/
│   │   └── portal.py                 # customer portal controllers
│   ├── views/                        # XML views, menus, portal templates
│   ├── security/                     # ACLs and record rules
│   ├── data/                         # sequences, email templates, products
│   ├── report/                       # PDF report templates
│   └── static/src/                   # POS OWL components (JS/XML)
│       └── overrides/
│           ├── components/
│           │   ├── product_screen/    # barcode scan handler
│           │   ├── payment_screen/    # post-payment hook
│           │   └── consign_card_popup/ # item selection popup
│           └── models/
│               └── pos_order.js       # order sync extension
├── woow_member_center/
│   ├── __manifest__.py
│   ├── models/
│   ├── controllers/
│   ├── views/                        # portal hub templates
│   ├── security/
│   └── static/src/css/              # responsive styles
├── docs/
│   ├── images/                       # screenshots
│   └── ARCHITECTURE.md               # detailed architecture docs
├── README.md                         # English documentation
├── README_zh-TW.md                   # 繁體中文文件
├── LICENSE                           # LGPL-3
└── CHANGELOG.md                      # version history
```

### Security & Access Control

| Model | Salesman | Manager | POS User | Portal |
|-------|----------|---------|----------|--------|
| `loyalty.consign.line` | CRUD (no delete) | Full CRUD | Read only | Read only |
| `loyalty.consign.redemption` | CRUD (no delete) | Full CRUD | Read + Create | Read only |
| `loyalty.consign.redemption.line` | CRUD (no delete) | Full CRUD | Read + Create | Read only |
| `loyalty.card` | (inherited) | (inherited) | (inherited) | Read only |

Portal users can only see their own records (row-level security via `ir.rule`).

---

## Roadmap

- [ ] Batch redemption — redeem from multiple cards in one POS transaction
- [ ] Expiration management — auto-notify when card items are nearing expiry
- [ ] Transfer — allow customers to transfer consigned items to another member
- [ ] Inventory integration — link consignment lines to warehouse stock moves
- [ ] Analytics dashboard — redemption trends, popular items, card utilization rates

---

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'feat: add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

Please ensure your code follows the [Odoo coding guidelines](https://www.odoo.com/documentation/18.0/contributing/development/coding_guidelines.html).

---

## License

This project is licensed under the **GNU Lesser General Public License v3.0 (LGPL-3)** — see the [LICENSE](LICENSE) file for details.

---

## Author

<p align="center">
  <b>WoowTech 沃科技</b><br>
  <a href="https://www.woow.tw">https://www.woow.tw</a><br>
  Odoo integration specialists — ERP, loyalty, POS, and e-commerce solutions
</p>
