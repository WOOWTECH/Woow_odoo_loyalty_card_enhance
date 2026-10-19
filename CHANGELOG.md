# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-10-19

收編 mujimed／inzense 兩站的站上修改（woow_paas_platform#1861）。

### Fixed

#### woow_member_center (18.0.2.0.1)
- Portal 首頁的會員中心入口卡片加上 `config_card`，計數為 0 時也顯示（與各子模組卡片一致；來自 mujimed）
- 明細歷史頁的麵包屑標籤（E-Wallet／Loyalty Card／Gift Card／Coupon）改為可翻譯，補 zh_TW

#### woow_mc_coupon／woow_mc_ewallet／woow_mc_gift_card／woow_mc_loyalty (18.0.1.0.1)
- zh_TW「Transaction History」統一譯為「交易紀錄」（與 woow_member_center 一致）

#### woow_loyalty_consign (18.0.8.0.1)
- 版號標記 PR #2 補上的 consign token model 唯讀 access rules

## [18.0.1.0.0] - 2026-05-07

### Added

#### woow_member_center
- Unified member center portal hub page
- Support for eWallet, loyalty points, gift cards, coupons, and membership
- Mobile-first responsive design with SVG icons
- Deep links to individual card detail pages
