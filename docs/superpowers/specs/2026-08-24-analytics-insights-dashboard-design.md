# 團隊測試分析與簡易 Dashboard 設計

日期：2026-08-24

## 目標

在不改變停車推薦結果的前提下，讓團隊能回答三個問題：

1. 查詢在哪個階段失敗或變慢？
2. 系統推薦了什麼，使用者最後選了什麼？
3. 哪些行政區、目的地與停車場最常被使用？

Dashboard 必須讓第一次打開的人在一分鐘內看懂，不建立通用分析平台，也不加入登入會員、第三方分析服務或複雜圖表。

## 已確認範圍

- 團隊測試環境預設啟用分析，保留既有匿名裝置 UUID 與 HMAC 雜湊。
- 修正地址查詢的行政區分析欄位；只影響分析，不縮小附近停車場搜尋範圍。
- 團隊 VM 的行政區樣本門檻設為 1；程式預設仍為 5，未來公開時可恢復。
- 保存查詢輸入、解析結果、分段耗時、推薦前三名、操作事件與簡單回饋。
- 原始輸入與完整目的地 14 天後清空；其餘分析資料 90 天後刪除。
- 既有 `analytics_events` 繼續使用，只新增兩張表。

## 不做

- 不保存 Cookie、Authorization、API Key、完整 HTTP Header 或持續 GPS 軌跡。
- 不保存 Gemini 的完整提示詞、完整模型回應或 Python traceback。
- 不建立任意事件／任意欄位的通用追蹤框架。
- 不加入圖表套件、即時輪詢、匯出 Excel、會員分群或 A/B 測試。
- 不回填目前已存在且 `district IS NULL` 的舊事件。

## 資料模型

### 1. `analytics_query_details`

每個 `request_id` 最多一筆，保存查詢生命週期摘要：

- 時間與識別：`request_id`、`occurred_at`、`anonymous_id_hash`、`source`
- 輸入與解析：`query_mode`、`raw_query_text`、`parsed_query_json`、`destination_label`、`district`、`arrival_time`、`intent`
- 結果：`outcome_code`、`error_stage`、`fallback_reason`、`data_status`、`result_count`、`location_choice_count`
- 分段耗時：`parse_ms`、`geocode_ms`、`freshness_ms`、`database_ms`、`walking_ms`、`total_ms`
- 資料時間：`official_data_at`、`collected_at`
- 使用結果：`feedback_code`，只接受 `found_space`、`full_on_arrival`、`did_not_go`

`raw_query_text` 最長 500 字；`parsed_query_json` 只保存固定結構化欄位，不保存模型原始回應。14 天後將 `raw_query_text`、`parsed_query_json` 與 `destination_label` 更新為 `NULL`，但保留彙總欄位至 90 天。

### 2. `analytics_recommendations`

每次成功查詢最多三筆，以 `(request_id, rank)` 為主鍵，保存當時推薦快照：

- 場站：`parking_lot_id`、`lot_name`、`rank`、`recommendation_group`
- 空位：`available_spaces`、`total_spaces`、`pressure_label`、`decision_status`
- 距離：`straight_distance_m`、`walking_distance_m`、`walking_minutes`、`distance_source`
- 說明：`hourly_fee_label`、`daily_cap_label`、`facility_type_label`
- 行為：`navigation_clicked_at`

保存快照而不是事後 JOIN 最新停車資料，避免空位與費率隨時間改變後扭曲歷史結果。

### 3. `analytics_events`

保留目前事件，允許下列固定新增事件：

- `location_choice_shown`
- `location_choice_selected`
- `map_marker_clicked`
- `history_opened`
- `navigation_clicked`
- `pwa_opened`

導航事件寫入後，同時以 `request_id + parking_lot_id` 標記推薦快照。每個 request／事件型態仍只保存第一筆，避免連續點擊灌高數字。

## 行政區推導

行政區只供分析，不回寫 `parsed["district"]`，避免地址位於行政區邊界時意外排除附近跨區停車場。

推導優先序：

1. 使用者明確選擇的合法行政區。
2. 解析後地址中的臺北市十二行政區。
3. 地址服務 `display_address` 中的臺北市十二行政區。
4. 都無法確認時保存 `NULL`，不以最近停車場猜測。

## 後端資料流

查詢開始時建立固定 `trace` 字典，只放允許欄位。各階段完成後更新分段耗時與結果；所有終端分支都經既有 `terminal()`：

1. 先以最佳努力寫入既有成功／失敗事件。
2. 再以獨立短交易 UPSERT `analytics_query_details`。
3. 成功產生推薦時，單次批次寫入前三名 `analytics_recommendations`。
4. 任一分析寫入失敗只寫不含輸入文字的警告，不影響公開查詢回應。

分析建構邏輯放在新的小型 `analytics_capture.py`；SQL 留在 `analytics_database.py`。`app.py` 只負責更新 trace 與呼叫，不在路由中堆疊 SQL 或統計公式。

## 使用者回饋

結果區顯示一個小型區塊：「這次推薦有幫助嗎？」

- 有，找到車位
- 到場已滿
- 沒有前往

前端只送 `request_id`、本機 UUID 與白名單 `feedback_code` 到 `POST /api/analytics/feedback`。後端驗證 UUID 與 request 對應後更新一次；重複點擊採最後一次選擇，查詢本身不受回饋 API 失敗影響。

## 簡易 Dashboard

保留 `今日／7 天／30 天` 切換，內容固定為四區，不加圖表：

### A. 一眼看懂 KPI

- 完成查詢
- 成功率
- 回應中位數／P95
- 導航點擊率
- 找到車位回饋率
- 匿名測試裝置

### B. 使用者去哪裡

- 熱門行政區：查詢數，不再只顯示裝置數
- 熱門目的地：最多 10 筆；14 天後名稱清空的資料不列入
- 最常被導航的停車場：名稱、次數、原推薦排名

### C. 系統哪裡需要改善

- 各階段耗時中位數：解析、地址、資料確認、MySQL、步行路線
- 失敗／降級原因與次數
- 模糊地點出現與選擇次數

### D. 最近查詢

最多 20 筆，顯示時間、輸入摘要、行政區、結果、總耗時、導航場站與回饋。輸入只在 14 天內可見；不顯示裝置雜湊。

空資料要顯示具體原因，例如「尚無行政區資料」或「需完成一次新查詢」，不用籠統的「本時段沒有資料」。

## API 與查詢效率

- 延伸既有 `GET /admin/api/analytics`，一次回傳 Dashboard 四區資料。
- 新增 `POST /api/analytics/feedback`。
- 所有列表固定上限，資料庫查詢按時間索引，禁止每筆推薦再查一次的 N+1。
- 最長範圍只有 30 天；管理 API 目標在目前 e2-micro 上 2 秒內完成。

## 清理與部署

既有每日清理程式擴充為：

1. 清空 14 天前查詢明細中的原始文字欄位。
2. 刪除 90 天前推薦快照、查詢明細與事件。

部署順序：完整 DB 備份、套用可重複執行 migration、部署程式、設定 `ANALYTICS_SEGMENT_MIN_DEVICES=1`、健康檢查與瀏覽器 QA。回復舊程式時新表可保留，不影響舊版執行；如需完全回復，再使用 DB 備份。

## 測試與驗收

- TDD 覆蓋 migration 合約、欄位白名單、行政區推導、前三名快照、回饋驗證、14／90 天清理與 Dashboard 彙整。
- 完整離線測試、`compileall`、`node --check`、`git diff --check` 全部通過。
- 實際瀏覽器完成「台北車站」查詢，確認中正區、前三名、分段耗時與最近查詢出現在 Dashboard。
- 點擊第一名導航與回饋「有，找到車位」，確認漏斗與場站排名更新。
- 驗證 Dashboard 未授權為 401、登入後為 200、管理 API 小於 2 秒。
- 驗證公開查詢即使分析資料庫寫入失敗仍正常回傳推薦。

## 複雜度限制

- 新增 production Python 檔案最多一個：`analytics_capture.py`。
- 新增資料表正好兩張，不再增加第三張。
- Dashboard 不新增圖表或前端框架。
- 新增 production 程式碼最終上限為淨增量（added − removed）≤ 950 行，
  raw added 與 removed 分開報告；若超過，必須先回來縮減範圍。
- 不改停車推薦、費率、步行排序、Gemini 或地理搜尋規則。
