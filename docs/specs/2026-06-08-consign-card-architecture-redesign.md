# 寄品卡架構調整 Design Spec

## Goal

將寄品卡從「禮品卡和電子錢包」分類中獨立出來，成為與禮品卡平級的獨立選單入口，精簡方案表單，整合相關資料到卡片層級。

## Architecture

### 選單結構

```
銷售 → 產品
  ├── 折扣與忠誠度        (seq 40) — 原生不動
  ├── 禮品卡和電子錢包     (seq 50) — 原生不動，移除 consign whitelist
  └── 寄品管理            (seq 55) — 獨立入口，單一選單
```

點擊「寄品管理」→ 開啟寄品方案列表（domain: program_type=consign）

### 寄品方案表單

精簡設計，跟禮品卡一樣只有幾個設定欄位：

```
┌─────────────────────────────────────┐
│ [Smart Button: 寄品卡 N 張]          │
├─────────────────────────────────────┤
│ 方案名稱                             │
│ 觸發產品                             │
│ Email 模板                           │
│ ─────── 限制使用 ─────────           │
│ 在以下可用: [網站/銷售點]             │
│ 移除: 價格表、貨幣                    │
└─────────────────────────────────────┘
```

### 寄品卡表單（不變，已完成）

```
┌─────────────────────────────────────┐
│ [重寄通知] [核銷]                    │
├─────────────────────────────────────┤
│ 卡號 | 客戶 | 寄品現值               │
├── 寄品明細 tab ─────────────────────┤
│ (只能新增不能改刪)                    │
├── 核銷紀錄 tab ─────────────────────┤
│ (核銷單列表)                         │
├── Chatter ──────────────────────────┤
│ (通知、核銷記錄、建立記錄)            │
└─────────────────────────────────────┘
```

## Changes

### 1. `views/menu_views.xml` — 精簡選單

**移除：**
- 所有子選單（寄品卡、寄品明細、核銷紀錄的獨立選單）
- POS、eCommerce 的重複選單

**保留：**
- Sales 頂層選單「寄品管理」（seq 55），指向 `loyalty_program_consign_action`

### 2. `views/loyalty_program_views.xml` — 獨立表單

**移除 consign 從 Gift/eWallet form whitelist：**
- 不再繼承 `loyalty_program_gift_ewallet_view_form`
- 建立獨立的寄品方案 form view（`loyalty_program_consign_view_form`）

**新 form view 內容：**
- `program_type`（隱藏，固定 consign）
- `name`（方案名稱）
- `trigger_product_ids`（觸發產品，many2many_tags）
- `mail_template_id`（Email 模板）
- `limit_usage` label + 欄位群組（加上「限制使用」標題）
- Smart button → 寄品卡數量

**隱藏/不顯示：**
- `currency_id`
- `pricelist_ids`
- Rules & Rewards tab
- Communications tab

### 3. `models/loyalty_program.py` — Smart button method

新增：
- `consign_card_count` computed field
- `action_view_consign_cards()` method

### 4. `views/loyalty_card_consign_views.xml` — 移除 history

已完成：history_ids 用 `invisible="is_consign"` 替換。

### 5. `__manifest__.py` — 版本升級

`18.0.1.1.0` → `18.0.1.2.0`

## Not Changed

- 寄品卡表單結構（已完成的寄品明細 + 核銷紀錄 tab）
- res.partner smart button（已完成）
- loyalty_consign_line 的 write protection（已完成）
- POS 核銷功能
