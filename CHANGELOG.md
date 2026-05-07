# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [18.0.1.0.0] - 2026-05-07

### Added

#### woow_loyalty_consign
- New `consign` (寄品卡) program type for `loyalty.program`
- Trigger product mechanism — auto-create consignment cards on sale order confirmation
- `loyalty.consign.line` model for per-item quantity tracking
- `loyalty.consign.redemption` and `loyalty.consign.redemption.line` models for audit trail
- Backend redemption wizard for manual item redemption
- POS barcode integration — scan card → select items → $0 redemption lines
- POS `_postPushOrderResolve` hook for post-payment redemption confirmation
- Customer portal pages for card detail and redemption history
- PDF report with barcode and item list
- Email notification on card creation
- Security: ACLs for salesman, manager, POS user, and portal groups
- Security: Row-level record rules for portal access

#### woow_member_center
- Unified member center portal hub page
- Support for eWallet, loyalty points, gift cards, coupons, membership, and consignment cards
- Mobile-first responsive design with SVG icons
- Deep links to individual card detail pages
