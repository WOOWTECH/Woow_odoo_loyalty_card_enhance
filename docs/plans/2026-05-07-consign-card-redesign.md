# 寄品卡架構重設計

日期：2026-05-07
狀態：設計中

## 設計決策

| 項目 | 決定 |
|------|------|
| 程式分組 | 從「折扣及會員計劃」移到「禮品卡及電子錢包」 |
| 點數模型 | 純品項制，不使用原生 loyalty.card.points |
| 觸發方式 | 觸發商品（「寄品服務」），訂單含此商品 → 建卡 |
| 轉入範圍 | 訂單內全部非觸發商品都轉為寄品明細 |
| 卡片數量 | 一客一卡，固定行為（移除 consign_one_card_per_partner 設定） |
| 客戶綁定 | Nominative，卡片綁定客戶 |
| POS 核銷 | 掃碼 → 彈 popup 選品 → 以 $0 加入 POS 訂單行 → 結帳時建核銷單 |
| 觸發商品價格 | 業務自訂，不影響程式邏輯 |

---

## 一、程式分組變更

### 目標

寄品卡 (`consign`) 從「折扣及會員計劃」的 `program_type` 下拉選單移到「禮品卡及電子錢包」的下拉選單。

### 原理

原生 Odoo 18 用兩個獨立的 form view 分割 program_type：

- **折扣及會員計劃** (`loyalty_program_view_form`)：`blacklisted_values: ['gift_card', 'ewallet']`
- **禮品卡及電子錢包** (`loyalty_program_gift_ewallet_view_form`)：`whitelisted_values: ['gift_card', 'ewallet']`

目前 `consign` 被 `selection_add` 加到 `program_type`，因為不在 blacklist 裡所以出現在折扣區。

### 變更

1. 繼承 `loyalty_program_gift_ewallet_view_form`，將 `whitelisted_values` 改為 `['gift_card', 'ewallet', 'consign']`
2. 繼承 `loyalty_program_view_form`，將 `blacklisted_values` 改為 `['gift_card', 'ewallet', 'consign']`
3. 在禮品卡/電子錢包的 form view 中加入寄品卡專屬欄位（invisible 控制）

---

## 二、觸發商品機制

### 目標

寄品卡跟電子錢包一樣，有一個「觸發商品」。訂單中包含此商品時，確認訂單即自動建卡並將其他商品行存入寄品明細。

### 資料模型變更

#### loyalty.program 新增/變更

```
trigger_product_ids  — 原生 related 欄位，已存在
                       在 consign 的 form view 中顯示為「寄品卡產品」
```

#### loyalty.program._program_type_default_values() 新增 consign 區段

```python
'consign': {
    'applies_on': 'future',
    'trigger': 'auto',
    'portal_visible': True,
    'portal_point_name': '品項',
    'rule_ids': [(5, 0, 0), (0, 0, {
        'reward_point_amount': 1,
        'reward_point_mode': 'unit',
        'reward_point_split': False,
        'product_ids': trigger_product,
        'minimum_qty': 0,
    })],
    'reward_ids': [(5, 0, 0)],  # 寄品卡無原生 reward
    'communication_plan_ids': [(5, 0, 0), (0, 0, {
        'trigger': 'create',
        'mail_template_id': consign_mail_template,
    })],
}
```

重點：
- `reward_ids` 清空 — 寄品卡不使用原生的折扣 reward 機制
- `applies_on = 'future'` — 跟 ewallet 一樣，未來訂單使用
- `trigger = 'auto'` — 購買觸發商品時自動建卡

### 移除的欄位

- `consign_one_card_per_partner` — 固定一客一卡，不需設定
- sale.order 上的 `auto_consign`, `consign_program_id` — 改用觸發商品機制

---

## 三、銷售訂單流程

### 確認訂單時的邏輯

```
action_confirm()
  → 偵測訂單行中是否有寄品方案的觸發商品
  → 如果有：
    1. 查找此客戶在該方案下的既有卡片
    2. 若無，建立新 loyalty.card（partner_id = 客戶）
    3. 遍歷所有「非觸發商品」的訂單行
    4. 對每行呼叫 card.consign_add_line(product, qty, unit_price, ...)
    5. 標記訂單行 is_consigned = True
    6. 發送寄品卡通知信
```

### 重要細節

- 一客一卡：用 `search([program, partner], limit=1)` 找既有卡，找不到才建新的
- 觸發商品本身不存入寄品明細
- `is_consigned` 欄位保留，防止重複處理

---

## 四、POS 核銷流程

### 前端 (OWL 元件)

```
1. 門市人員在 POS 掃描 barcode 或輸入寄品卡號
2. 系統呼叫後端 RPC: /pos/consign/lookup
   → 回傳卡片資訊 + 所有 state='active' 的寄品明細
3. 彈出 ConsignRedeemPopup：
   - 顯示客戶名稱、卡號
   - 列出品項清單（品名、剩餘數量）
   - 每行有勾選框 + 數量輸入
4. 人員勾選品項、填入數量 → 點「確認」
5. 選中的品項以 $0 加入 POS 訂單行
   - product_id = 原品項
   - price_unit = 0
   - qty = 核銷數量
   - 標記 consign_redeem_line_id（關聯寄品明細行）
6. POS 訂單結帳確認
7. 後端 hook：偵測有 consign_redeem 標記的行
   → 建立 loyalty.consign.redemption + lines
   → 自動 action_done()
```

### 後端 API

新增 POS 專用 controller：

```python
@http.route('/pos/consign/lookup', type='json', auth='user')
def pos_consign_lookup(self, barcode):
    """用 barcode 查詢寄品卡及有效品項"""

@http.route('/pos/consign/redeem', type='json', auth='user')
def pos_consign_redeem(self, card_id, lines):
    """建立核銷單"""
```

### POS 訂單行擴展

```
pos.order.line 新增：
- is_consign_redeem: Boolean
- consign_line_id: Many2one → loyalty.consign.line
```

---

## 五、is_payment_program 處理

原生 `is_payment_program` 計算：
```python
program.is_payment_program = program.program_type in ('gift_card', 'ewallet')
```

寄品卡**不應該**被視為 payment program，因為：
- 不使用 points 抵扣金額
- 不產生折扣行
- POS 核銷是 $0 訂單行，不是扣款

需要確保 `consign` 不被加入 `is_payment_program` 的判斷。

---

## 六、檔案變更清單

### 修改的檔案

| 檔案 | 變更 |
|------|------|
| `models/loyalty_program.py` | `_program_type_default_values` 加入 consign 預設值；移除 `consign_one_card_per_partner` |
| `models/loyalty_card.py` | `consign_add_line` 固定一客一卡邏輯（移除 one_card_per_partner 判斷） |
| `models/sale_order.py` | 重寫 `_action_create_consign_card`：改用觸發商品偵測；移除 `auto_consign` / `consign_program_id` |
| `views/loyalty_program_views.xml` | 改繼承 gift_ewallet form view；加入 `whitelisted_values` 修改 |
| `views/sale_order_views.xml` | 移除 `auto_consign` / `consign_program_id` 欄位；保留寄品按鈕 |
| `views/menu_views.xml` | 移除獨立的寄品卡程式選單（改在禮品卡/電子錢包區管理） |

### 新增的檔案

| 檔案 | 用途 |
|------|------|
| `static/src/js/consign_redeem_popup.js` | POS 核銷 popup OWL 元件 |
| `static/src/js/consign_redeem_popup.xml` | POS popup 模板 |
| `static/src/js/pos_barcode.js` | POS barcode 掃描 hook |
| `controllers/pos_consign.py` | POS API endpoints |
| `models/pos_order.py` | pos.order / pos.order.line 擴展 |

### 移除的欄位

| Model | 欄位 | 原因 |
|-------|------|------|
| `loyalty.program` | `consign_one_card_per_partner` | 固定一客一卡 |
| `sale.order` | `auto_consign` | 改用觸發商品 |
| `sale.order` | `consign_program_id` | 改用觸發商品 |
| `sale.order` | `consign_card_id` (computed) | 不再需要 |

---

## 七、開發階段

### 第一階段：程式分組 + 觸發商品

1. 修改 `program_type` 在 view 中的分組（blacklist/whitelist）
2. 加入 `_program_type_default_values` 的 consign 區段
3. 重寫 sale order 確認流程（觸發商品偵測）
4. 移除舊的 `auto_consign` / `consign_program_id` 機制
5. 調整 form view：觸發商品欄位、移除舊欄位

### 第二階段：POS 核銷

1. 建立 POS API endpoints
2. 開發 OWL popup 元件
3. 擴展 pos.order.line
4. POS barcode hook
5. 結帳確認時自動建核銷單

### 第三階段：測試與收尾

1. 端到端測試：建方案 → 設觸發商品 → 下單 → 確認 → 建卡 → POS 核銷
2. Portal 頁面驗證
3. 報表驗證
