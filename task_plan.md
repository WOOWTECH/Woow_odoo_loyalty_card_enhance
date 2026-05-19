# Task Plan: Portal 會員中心模組化拆分

## Goal
將 `woow_member_center` 從單體模組拆分成 7 個獨立 Odoo 模組，實現 portal user 頁面各功能可獨立安裝/顯示，並加入資料層過濾（使用者無資料時隱藏卡片）。

## Current Phase
Phase 1

## Phases

### Phase 1: Hub 核心模組重構
- [ ] 精簡 `woow_member_center/__manifest__.py`（移除 `membership`、`woow_loyalty_consign` 依賴）
- [ ] 重構 `controllers/portal.py`：只保留 hub route + `_prepare_hub_values()` 方法 + history route
- [ ] 重構 `views/portal_templates.xml`：只保留 hub 入口 + 空 grid 容器 + 空狀態提示
- [ ] 保留共用元件：`loyalty_history_snippet.xml`、`loyalty_history_page.xml`、`loyalty_history.py`、CSS
- [ ] 更新 `security/portal_security.xml`：移除 membership rule（搬到 woow_mc_membership）
- [ ] 更新 CSS 加入卡片排序 + 空狀態樣式
- **Status:** pending

### Phase 2: 建立 woow_mc_ewallet 子模組
- [ ] 建立目錄結構：`__init__.py`、`__manifest__.py`、`controllers/`、`views/`、`static/`
- [ ] 搬移 `ewallet_templates.xml` → 加入 hub xpath 注入 + `t-if="show_ewallet"`
- [ ] 搬移 `ewallet.svg`
- [ ] 建立 controller：繼承 `MemberCenterPortal`，覆寫 `_prepare_hub_values()` + ewallet routes
- [ ] 加入 breadcrumb xpath
- **Status:** pending

### Phase 3: 建立 woow_mc_loyalty 子模組
- [ ] 同 Phase 2 結構，搬移 loyalty 相關檔案
- [ ] controller 覆寫 `_prepare_hub_values()` + loyalty routes
- **Status:** pending

### Phase 4: 建立 woow_mc_gift_card 子模組
- [ ] 同 Phase 2 結構，搬移 gift_card 相關檔案
- [ ] controller 覆寫 `_prepare_hub_values()` + gift_card routes
- **Status:** pending

### Phase 5: 建立 woow_mc_coupon 子模組
- [ ] 同 Phase 2 結構，搬移 coupon 相關檔案
- [ ] controller 覆寫 `_prepare_hub_values()`（加 `points > 0` 過濾）+ coupon routes
- **Status:** pending

### Phase 6: 建立 woow_mc_consign 子模組
- [ ] 建立目錄結構
- [ ] 從 `woow_loyalty_consign` 搬移 portal controller + portal templates
- [ ] 加入 hub xpath 注入 + `t-if="show_consign"`
- [ ] 搬移 `consign.svg`
- [ ] 搬移 consign portal security rules 到此模組
- **Status:** pending

### Phase 7: 建立 woow_mc_membership 子模組
- [ ] 建立目錄結構
- [ ] 搬移 `membership_templates.xml`、`membership.svg`
- [ ] 搬移 membership portal security rule
- [ ] controller 覆寫 `_prepare_hub_values()`（永遠顯示，無條件）
- [ ] 搬移 membership route
- **Status:** pending

### Phase 8: 清理與驗證
- [ ] 移除根目錄重複模組副本（只保留 `/addons/`）
- [ ] 驗證所有 `__manifest__.py` 依賴正確
- [ ] 驗證 template XML ID 引用路徑正確（跨模組引用需用完整 module.template_id）
- [ ] 驗證 controller 繼承鏈正確（Odoo MRO 順序）
- [ ] 確認 `woow_loyalty_consign` 後端邏輯不受影響
- **Status:** pending

### Phase 9: 測試
- [ ] 語法驗證（Python + XML lint）
- [ ] 確認模組可各自獨立安裝
- [ ] 確認 hub 頁面根據安裝的模組動態顯示卡片
- [ ] 確認資料層過濾（無資料時隱藏）
- [ ] 確認 breadcrumb 導航正確
- **Status:** pending

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 拆成 7 個獨立模組 | 最大靈活度，每個功能可獨立安裝/解除 |
| Hub 依賴 portal + loyalty | 共用 history 元件需要 loyalty model |
| 統一前綴 woow_mc_ | 模組列表容易辨識歸類 |
| _prepare_hub_values() 繼承鏈 | Odoo 標準做法，子模組透過 super() 疊加資料 |
| xpath 注入 hub grid | Odoo 標準 template inheritance |
| data-mc-order 排序 | 不受模組載入順序影響 |
| 優惠券條件: points > 0 | 只顯示尚未使用的 coupon |
| 會員資格: 永遠顯示 | 只要模組安裝就顯示，不判斷資料 |
| consign portal 從 woow_loyalty_consign 搬出 | 分離展示層與後端邏輯 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|

## Notes
- 設計文件位於 `docs/plans/2026-05-19-portal-modular-hub-design.md`
- 根目錄和 /addons/ 有重複模組，最終只保留 /addons/
- woow_loyalty_consign 後端邏輯完全不動
