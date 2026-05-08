# POS 寄品卡核銷功能 - 全面行為測試報告

**測試日期**: 2026-05-08
**測試模組**: `woow_loyalty_consign`
**測試環境**: Odoo 18 POS (http://localhost:9100)
**測試工具**: Playwright CLI 自動化瀏覽器操作
**POS 配置**: Furniture Shop (config_id=1)
**測試客戶**: Joel Willis (partner_id=8)

---

## 測試卡片資料

| 卡片類型 | 卡片 ID | 卡碼 | 初始數量 | 初始金額 |
|----------|---------|------|----------|----------|
| 酒窖寄存 | 91 | 0443-e50f-4796 | 8 | $37,000 |
| 醫美療程 | 75 | 0440-6907-4d9f | 9 | $31,500 |
| 寄杯方案 | 77 | 0446-2553-46fe | 12 | $2,010 |

---

## 測試結果摘要

| 階段 | 測試項目 | 結果 | 截圖 |
|------|---------|------|------|
| Phase 1 | 掃碼查詢 - 3 種卡 | PASS | 01-03 |
| Phase 2 | 酒窖卡核銷 + 結帳 | PASS | 05-08 |
| Phase 3 | 醫美卡核銷 + 結帳 | PASS | 09-11 |
| Phase 4 | 寄杯卡核銷 + 結帳 | PASS | 12-13 |
| Phase 5 | 多品項同時核銷 | PASS | 14-16 |
| Phase 6 | 邊界測試 (無效碼) | PASS | 17-18 |
| Phase 7 | 超額核銷防護 | PASS | 19 |
| Phase 8 | 混合訂單 | PASS | 20-22 |
| Phase 9 | 後端資料驗證 | PASS | — |

**總計: 9/9 階段全部通過**

---

## 詳細測試過程

### Phase 1: 掃碼查詢

在 POS 銷售畫面設定客戶 Joel Willis 後，分別掃描三種卡碼：

1. **酒窖寄存卡** (`0443-e50f-4796`): 彈出「寄品卡核銷 - 酒窖寄存方案」視窗，顯示 2 筆 Chateau Margaux 2018 明細（qty=2 和 qty=6），含到期日 2028-05-08
2. **醫美療程卡** (`0440-6907-4d9f`): 彈出「寄品卡核銷 - 醫美療程方案」視窗，顯示皮秒雷射單次（qty=9）
3. **寄杯方案卡** (`0446-2553-46fe`): 彈出「寄品卡核銷 - 寄杯方案」視窗，顯示抹茶拿鐵（qty=5）和精品手沖咖啡（qty=7），到期日 2027-05-08

> 截圖: `01_wine_card_popup.png`, `02_aesthetics_card_popup.png`, `03_general_card_popup.png`

### Phase 2: 酒窖卡正常核銷

- 掃描酒窖卡 → 點擊 + 按鈕選取 1 瓶 Chateau Margaux 2018
- OWL 反應式狀態正確更新，確認核銷按鈕啟用
- 點擊「確認核銷」→ 通知「已加入 1 項寄品核銷品項」
- 訂單明細顯示：「寄品核銷 $0.00 1.00 x $0.00 / Units [寄品] Chateau Margaux 2018」
- 付款 → 核實 → 「付款成功」，收據正確顯示

> 截圖: `05_wine_redeem_qty1_ready.png` → `06_wine_order_line_added.png` → `07_wine_payment_screen.png` → `08_wine_payment_success.png`

### Phase 3: 醫美卡正常核銷

- 新訂單 → 設定客戶 Joel Willis → 掃描醫美卡
- 選取 1 次皮秒雷射 → 確認核銷
- 訂單明細：「寄品核銷 $0.00 [寄品] 皮秒雷射單次」
- 付款成功

> 截圖: `09_aesthetics_card_popup.png` → `10_aesthetics_order_line.png` → `11_aesthetics_payment_success.png`

### Phase 4: 寄杯卡正常核銷

- 新訂單 → 設定客戶 → 掃描寄杯卡
- 選取 1 杯抹茶拿鐵 → 確認核銷
- 付款成功

> 截圖: `12_general_card_popup.png` → `13_general_payment_success.png`

### Phase 5: 多品項同時核銷

- 新訂單 → 設定客戶 → 掃描寄杯卡
- 同時選取：1x 抹茶拿鐵 + 2x 精品手沖咖啡
- 確認核銷 → 通知「已加入 **2** 項寄品核銷品項」
- 訂單明細出現兩行：
  - 寄品核銷 [寄品] 抹茶拿鐵 (qty=1)
  - 寄品核銷 [寄品] 精品手沖咖啡 (qty=2)
- 付款成功

> 截圖: `14_multi_item_selection.png` → `15_multi_item_order_lines.png` → `16_multi_item_success.png`

### Phase 6: 邊界測試

**6a. 無客戶掃碼**: 未設定訂單客戶時掃描酒窖卡碼 → 彈窗仍正常顯示（因為卡片本身關聯客戶 Joel Willis）。此行為合理，POS 人員可先查詢卡片再決定是否核銷。

**6b. 無效碼**: 掃描 `XXXX-YYYY-ZZZZ` → 觸發 POS 標準錯誤通知「未知條碼 XXXX-YYYY-ZZZZ - 銷售點無法找到與掃描條碼相關的任何產品、客戶、員工或操作。」→ 正確 fallback 至 Odoo 原生掃碼處理

> 截圖: `17_no_customer_scan_works.png`, `18_invalid_barcode_error.png`

### Phase 7: 超額核銷防護

- 掃描酒窖卡（第一筆 Margaux 剩餘 1 瓶）
- 連續點擊 + 按鈕 3 次
- 數量始終被限制在 **1**（不超過可用數量）
- `increment()` 方法正確檢查 `line.qty_to_redeem < line.remaining_qty`

> 截圖: `19_over_redeem_capped.png`

### Phase 8: 混合訂單

- 先添加一般商品 Desk Pad ($1.98)
- 再掃描醫美卡，選取 1 次皮秒雷射核銷
- 訂單同時包含：
  - Desk Pad $1.98（一般商品）
  - 寄品核銷 $0.00 [寄品] 皮秒雷射單次（核銷品項）
- 付款 $1.98 現金 → 付款成功
- 收據正確顯示兩種品項

> 截圖: `20_mixed_order_lines.png` → `21_mixed_order_payment.png` → `22_mixed_order_success.png`

### Phase 9: 後端資料驗證

#### 核銷紀錄（POS 核銷，ID >= 22）

| 核銷單號 | 卡片 | 品項 | 數量 | 金額 | 階段 |
|----------|------|------|------|------|------|
| RD-202605-0022 | 酒窖 91 | Chateau Margaux 2018 | 1 | $5,000 | Phase 2 |
| RD-202605-0023 | 醫美 75 | 皮秒雷射單次 | 1 | $3,500 | Phase 3 |
| RD-202605-0024 | 寄杯 77 | 抹茶拿鐵 | 1 | $150 | Phase 4 |
| RD-202605-0025 | 寄杯 77 | 抹茶拿鐵 + 精品手沖咖啡 | 1+2 | $510 | Phase 5 |
| RD-202605-0026 | 醫美 75 | 皮秒雷射單次 | 1 | $3,500 | Phase 8 |

#### 卡片餘額驗證

| 卡片 | 初始qty / 初始val | 核銷qty / 核銷val | 期望qty / 期望val | 實際qty / 實際val | 結果 |
|------|-------------------|-------------------|-------------------|-------------------|------|
| 酒窖 91 | 8 / $37,000 | 1 / $5,000 | 7 / $32,000 | **7 / $32,000** | PASS |
| 醫美 75 | 9 / $31,500 | 2 / $7,000 | 7 / $24,500 | **7 / $24,500** | PASS |
| 寄杯 77 | 12 / $2,010 | 4 / $660 | 8 / $1,350 | **8 / $1,350** | PASS |

---

## 修復的 Bug

### POS 限制類別導致核銷商品無法載入

**問題**: POS Config 1 (Furniture Shop) 設定 `limit_categories=True`，限制 POS 可用產品僅載入特定類別。核銷商品「寄品核銷」(product id=98) 未設定 `pos_categ_ids`，導致 POS session 載入時被排除。前端 `this.pos.models["product.product"].get(redemptionProductId)` 回傳 `null`，核銷功能無法使用。

**錯誤訊息**: 「找不到寄品核銷商品，請確認已安裝模組。」

**修復方案**: 新增 `models/product.py`，覆寫 `product.product._load_pos_data()` 方法，強制將核銷商品注入 POS 資料集，無視類別篩選。此方法參考 `pos_loyalty` 模組的相同模式。

**修改檔案**:
- `models/product.py` (新增)
- `models/__init__.py` (新增 import)

---

## 技術筆記

- **OWL 按鈕互動**: POS 使用 OWL 框架，按鈕必須透過 `dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))` 觸發，才能正確更新反應式狀態
- **Modal 不可見於 Snapshot**: `o_technical_modal` 類別的對話框不會出現在 playwright-cli 的 snapshot 中，需使用 `page.evaluate()` 直接操作 DOM
- **Modal 關閉**: OWL 對話框可靠的關閉方式是按 Escape 鍵
- **條碼掃描模擬**: 透過快速逐字元鍵入（10ms 間隔）後按 Enter，模擬實體條碼掃描器行為
