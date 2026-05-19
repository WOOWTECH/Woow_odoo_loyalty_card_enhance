# Findings: Portal 會員中心模組化拆分

## 現有架構分析

### woow_member_center（單體模組）
- **依賴:** portal, loyalty, sale_loyalty, membership, woow_loyalty_consign
- **Controller:** 1 個檔案 421 行，包含 hub + 5 種卡片 + membership + history 共 11 個 route
- **Templates:** 9 個 XML 檔案，hub + 6 個功能模板 + 2 個共用 history 模板
- **CSS:** 1 個 145 行，hub grid + balance hero 樣式
- **SVG 圖示:** 7 個（hub + 6 功能）
- **Security:** 1 個 membership portal rule
- **Model:** loyalty_history.py 擴展 loyalty.history

### woow_loyalty_consign（寄品卡後端 + Portal 混合）
- **依賴:** loyalty, sale_loyalty, pos_loyalty, website_sale_loyalty, stock, portal, mail
- **Portal Controller:** ConsignPortal 84 行，3 個 route
- **Portal Templates:** 330 行，3 個模板（列表 + 詳情 + 核銷詳情）
- **Portal Security:** 4 個 portal rules
- **後端:** 10 個 model 檔案、POS 覆寫、wizard、reports

### Hub 頁面卡片渲染
- hub controller 直接查詢全部 6 種資料
- 模板硬編碼 6 張卡片，無條件全部渲染
- 寄品卡連結到 `/my/consign-cards`（woow_loyalty_consign 的 route）
- 其他 5 個連結到 `/my/member-center/xxx`

### 根目錄重複
- 根目錄和 /addons/ 各有一份 woow_member_center 和 woow_loyalty_consign
- 看起來是相同副本

## 共用元件清單

| 元件 | 被哪些功能使用 | 歸屬決定 |
|------|--------------|---------|
| loyalty_history_snippet.xml | ewallet, loyalty, gift_card, coupon 的 detail 頁面 | hub 核心 |
| loyalty_history_page.xml | 所有有 history 的卡片 | hub 核心 |
| loyalty_history.py | history snippet/page 需要的 helper | hub 核心 |
| member_center.css | hub grid + balance hero（所有頁面） | hub 核心 |
| hooks.py (post_init_hook) | 初始化 balance 欄位 | hub 核心 |

## 模組間跨引用

### Template 跨模組引用（需改路徑）
- ewallet/loyalty/gift_card/coupon detail 頁面用 `t-call="woow_member_center.portal_loyalty_history_table"` → 保持不變（hub 核心保留這個 template）
- consign portal 模板的 breadcrumb 引用 `portal.portal_breadcrumbs` → 保持不變
- hub 入口引用 `portal.portal_my_home` → 保持不變

### Controller 繼承
- 目前 ConsignPortal 繼承 CustomerPortal
- 拆分後改為繼承 MemberCenterPortal（hub 核心提供）
- 各子模組 controller 都繼承 MemberCenterPortal

## Odoo 技術注意事項

### xpath position="inside" 排序問題
- 多個模組用 xpath inside 注入同一個 div，順序由模組載入順序決定
- 解法：用 `data-mc-order` + CSS `order` 屬性強制排序

### Odoo MRO（Method Resolution Order）
- 多個 controller 繼承同一個 class 時，Odoo 用 Python MRO 決定 super() 呼叫順序
- 所有子模組的 _prepare_hub_values() 都需要呼叫 super() 確保鏈式疊加
