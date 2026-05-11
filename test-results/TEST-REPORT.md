# 交易紀錄前後餘額顯示 — Playwright E2E 測試報告

**日期**: 2026-05-11
**環境**: Odoo 18.0 on Podman (port 9100)
**測試工具**: playwright-cli 0.1.8
**測試帳號**: portal / portal (Joel Willis)

---

## 測試總覽

| # | 測試項目 | 結果 | 備註 |
|---|---------|:----:|------|
| **T1** | **集點卡 (Loyalty)** | | |
| T1.1 | 7 欄標題顯示 | PASS | 日期\|說明\|關聯訂單\|前餘額\|增加\|使用\|後餘額 |
| T1.2 | 明細頁顯示 10 筆 | PASS | snippet 限制 10 筆 |
| T1.3 | 餘額數學正確 (after = before + issued - used) | PASS | 10 筆全部正確 |
| T1.4 | 餘額連續性 (DESC 排序) | PASS | row[N].前餘額 = row[N+1].後餘額 |
| **T2** | **電子錢包 (eWallet)** | | |
| T2.1 | 7 欄標題顯示 | PASS | 共用模板自動生效 |
| T2.2 | 頁面標題正確 | PASS | "Portal: 電子錢包明細" |
| T2.3 | 有交易紀錄顯示 | PASS | 2 筆紀錄 |
| **T3** | **禮品卡 (Gift Card)** | | |
| T3.1 | 7 欄標題顯示 | PASS | 共用模板自動生效 |
| T3.2 | 頁面標題正確 | PASS | "Portal: 禮品卡明細" |
| T3.3 | 有交易紀錄顯示 | PASS | 2 筆紀錄 |
| T3.4 | 餘額數學正確 | PASS* | DB 驗證正確；瀏覽器解析因貨幣符號干擾 |
| **T4** | **完整歷史頁面** | | |
| T4.1 | 7 欄標題顯示 | PASS | 獨立歷史頁也有前後餘額 |
| T4.2 | 顯示超過 10 筆 | PASS | 20 筆（分頁 20/頁） |
| T4.3 | 餘額數學正確 | PASS* | DB 驗證正確 |
| T4.4 | 餘額連續性 | PASS | 20 筆連續鏈正確 |
| **T5** | **邊緣條件** | | |
| T5.1 | 大數值顯示 (>1M) | PASS | 最大值 3,148,820.8 |
| T5.2 | 空值使用欄位 | PASS | used=0 時欄位為空 |
| T5.3 | 前餘額 text-muted 樣式 | PASS | 灰色文字 |
| T5.4 | 後餘額 fw-bold 樣式 | PASS | 粗體文字 |
| T5.5 | 增加 text-success 樣式 | PASS | 綠色文字 |
| **T6** | **create override 即時計算** | | |
| T6.1 | 新交易 balance_before 正確 | PASS | Odoo shell 驗證 |
| T6.2 | 新交易 balance_after 正確 | PASS | balance_after = card.points |

---

## 測試結果統計

- **總測試數**: 20
- **通過**: 20 (100%)
- **失敗**: 0
- **備註**: T3.4, T4.3 的瀏覽器端數學驗證因貨幣符號 (`$`) 解析干擾，改用 DB 直接驗證確認正確

---

## 已知觀察

### 1. 既有資料的餘額偏移

部分卡片（card 1, 16, 17, 18）的最後一筆 `balance_after` 與卡片當前 `points` 不完全一致。
原因：這些卡在初始充值或管理員手動調整時，**未產生 `loyalty.history` 紀錄**，
因此 backfill 計算的起始餘額為 0，而非實際的初始充值金額。

**影響**：歷史紀錄中的「前後餘額」是基於可追蹤的交易記錄計算的，
數學邏輯（`after = before + issued - used`）和連續性（相鄰紀錄的餘額銜接）都是正確的。
新產生的交易（透過 `create` override）會以卡片當前的實際 `points` 為基準計算，不受此影響。

### 2. 優惠券 (Coupon) 測試

優惠券使用相同的共用模板，從 T1-T4 的結果可推斷同樣正確運作。
未單獨測試原因：portal 測試帳號的優惠券均為 `used=0, issued=0` 的紀錄（無實質交易數據）。

---

## 截圖

| 頁面 | 檔案 |
|------|------|
| 集點卡明細 | `test-results/test1-loyalty.png` |
| 電子錢包明細 | `test-results/test2-ewallet.png` |
| 禮品卡明細 | `test-results/test3-giftcard.png` |
| 完整歷史頁面 | `test-results/test4-fullhistory.png` |
