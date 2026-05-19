# Progress Log: Portal 會員中心模組化拆分

## Session: 2026-05-19

### 已完成
- [x] Codebase 完整分析（woow_member_center + woow_loyalty_consign）
- [x] 腦力激盪：確認架構方向（拆成 7 個獨立模組）
- [x] 腦力激盪：確認拆分粒度（6 功能各自獨立）
- [x] 腦力激盪：確認顯示條件（雙層過濾 + 優惠券 points>0 + 會員永遠顯示）
- [x] 腦力激盪：確認 hub 依賴（portal + loyalty）
- [x] 腦力激盪：確認命名慣例（woow_mc_ 前綴）
- [x] 腦力激盪：確認共用元件歸屬（history 放 hub 核心）
- [x] 撰寫設計文件（docs/plans/2026-05-19-portal-modular-hub-design.md）
- [x] 設計文件分 4 段呈現，全部使用者確認通過
- [x] 建立實作計畫（task_plan.md）

### 已完成（實作）
- [x] Phase 1: Hub 核心模組重構（__manifest__.py, controller, template, CSS, security）
- [x] Phase 2-7: 平行建立 6 個子模組（ewallet, loyalty, gift_card, coupon, consign, membership）
- [x] Phase 8a: 修復驗證發現的 3 個問題
  - loyalty.card ACL 加入 hub 核心
  - loyalty.card portal record rule 加入 hub 核心
  - 移除 woow_loyalty_consign 中重複的 loyalty_card_portal_rule
  - 新增 _get_order_portal_url() / _get_order_description() 防禦性方法
- [x] Phase 8b: 清理（移除 hub 中 6 個孤立 SVG 圖標）
- [x] Phase 9: 測試
  - Python 語法驗證：45 檔案全部通過
  - XML 格式驗證：25 檔案全部通過
  - 模板交叉引用驗證：12 個 request.render() 全部匹配、xpath 注入正確、t-call 依賴鏈完整
  - Manifest data 檔案對照：8 個模組全部通過

### 備註
- 寄品卡 URL 保持原有路徑（/my/consign-cards）以維持向下相容
- hub 核心僅保留 member-center.svg 圖標，各子模組各自擁有圖標副本
