# QA Review — 2026-08-23 匿名分析儀表板（Task 8 全功能隱私、迴歸與品質閘）

- 日期：2026-08-24（台北時區）
- 分支工作樹：`.worktrees/parking-analytics-dashboard`
- 結果：**通過**。所有離線檢查綠燈；隱私/秘密掃描逐項檢視無外洩；本機自動化驗收全數通過；無部署、無合併、無推送。
- 最終測試數：**285 passed**（Task 8 前基線 280 + 新增 5 個驗收測試）。

## 1. 離線自動化檢查（Step 1）

在乾淨 shell 依序執行：

```bash
python -m pytest -q
python -m compileall -q .
node --check static/app.js
node --check static/admin_analytics.js
```

結果：

- `pytest`：`285 passed in 2.08s`（最終驗證重跑，見第 8 節）。
- `compileall`：靜默，exit 0，無任何警告。
- `node --check static/app.js`：exit 0。
- `node --check static/admin_analytics.js`：exit 0。

無警告、無 skip、無 xfail。CI 合約測試（`tests/test_ci_contract.py`）同時確認 push/PR workflow 內含 pytest、compileall 與 Node 檢查。

## 2. 隱私與秘密掃描（Step 2）

執行指令（與 brief 完全一致）：

```bash
rg -n "address|destination|message|latitude|longitude|remote_addr|x_forwarded_for" analytics_service.py analytics_database.py status_service.py templates/admin_analytics.html static/admin_analytics.js deploy/nginx-parking-radar-log-format.conf
git grep -nE "ANALYTICS_HMAC_SECRET=.{8,}|auth_basic_user_file.*(Downloads|Users)"
```

逐筆檢視結果：

1. `deploy/nginx-parking-radar-log-format.conf:7` `$binary_remote_addr`：僅作為 `limit_req_zone` 的記憶體內限流鍵，**不進入任何日誌格式**；該檔案另有中文註解說明，且 `test_access_log_format_omits_client_identity_and_query_string` 斷言 log_format 不含 `$remote_addr`、`$http_x_forwarded_for`、`$binary_remote_addr` 與 `$request_uri`。
2. `analytics_service.py:40-44` `coarse_area_bucket`：座標立即降為 2 位小數（約 1 公里）網格，註解明示「輸出不含原始座標」；原始 `latitude`/`longitude` 從未進入事件字典或資料表。
3. `analytics_service.py:70-139`：事件建構器只接受白名單欄位，`_build_event` 固定 16 鍵，無 address/destination/message 自由文字；`source` 非白名單時降為 `unknown`。
4. `git grep` 三個命中：
   - `README.md:163`：部署說明，指示把 `openssl rand -hex 32` 產生的秘密貼到 VM 的 `/opt/parking-hell/.env`（伺服器路徑，非個人路徑）。
   - `tests/test_analytics_routes.py:449`：測試以空字串關閉分析（`ANALYTICS_HMAC_SECRET=""`）。
   - `tests/test_deploy_analytics_contract.py:58`：斷言 `.env.example` 只含空值的 `ANALYTICS_HMAC_SECRET=` 佔位。
   - `auth_basic_user_file.*(Downloads|Users)`：**零命中**；htpasswd 路徑為伺服器端 `/etc/nginx/.htpasswd-parking-radar`。

補充掃描（`BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY|AKIA|ghp_`、`password[:=]`、`api_key[:=]`）：所有命中皆為 `Config.*` 環境變數參考，無任何硬編碼秘密。

### 受檢檔案

- `analytics_service.py`、`analytics_database.py`、`status_service.py`、`analytics_cleanup.py`、`config.py`、`app.py`（分析寫入與管理路由區段）
- `templates/admin_analytics.html`、`static/admin_analytics.js`、`static/app.js`
- `migrations/20260823_add_analytics_events.sql`（16 個固定分類欄位，無自由文字/IP 欄位）
- `deploy/nginx-parking-radar-log-format.conf`、`deploy/nginx-parking-radar.conf`、`deploy/parking-radar.service`
- `.env.example`、`README.md`（部署補充區段）
- `tests/test_deploy_analytics_contract.py`、`tests/test_frontend_contract.py`、`tests/test_analytics_routes.py`、`tests/test_analytics_database.py`

結論：無硬編碼/個人秘密、無個人路徑、無 IP 日誌 token、無自由文字分析持久化、管理端程式碼不渲染任何目的地址或原始輸入。

## 3. 迴歸：分析寫入失敗不得破壞停車查詢（Step 3）

既有測試 `tests/test_analytics_routes.py::test_query_survives_writer_exception`（Task 2 加入）已證明 `/api/query` 在 writer 拋錯時仍回 200 且回應相同（`recommendations[0]["lot_id"] == "TPE1"`）。本任務發現其缺口：只斷言 `"analytics_write_failed" in caplog.text`，未證明「只記錄固定 event_type、不含輸入目的地」。已**加強既有測試而非複製**，新增斷言：

```python
failures = [record.getMessage() for record in caplog.records
            if "analytics_write_failed" in record.getMessage()]
assert failures == ["analytics_write_failed event=query_completed"]
assert "臺北市政府" not in caplog.text
```

此測試先跑（行為已正確，無需生產修復，屬純測試覆蓋補強）；`app.py` 的 `write_analytics_safely` 只記錄 `event["event_type"]`，與斷言一致。同檔 `test_event_endpoint_survives_writer_exception` 涵蓋事件端點 204 契約。

## 4. 本機路由／測試夾具驗收（Step 4）

自動化證據（全部通過，非 live VM）：

| 驗收項目 | 自動化證據（測試） |
| --- | --- |
| 無秘密：公開查詢可用、狀態顯示停用 | `test_query_without_secret_works_and_never_writes_analytics`（新增）、`test_analytics_api_honestly_empty_without_secret` |
| 無事件：儀表板零／空狀態 | `test_analytics_api_empty_events_with_secret_returns_zero_summary`（新增）、`test_empty_summary_returns_safe_none_and_zero_values`、`test_admin_analytics_js_renders_empty_and_disabled_states`（新增） |
| 資料庫失敗：只有 DB／資料元件紅灰 | `test_status_api_degrades_each_component_independently`、`test_status_api_handles_database_connection_failure`、`test_analytics_api_returns_503_when_database_read_fails`、`test_build_status_degrades_database_and_data_independently` |
| 五裝置門檻：四裝置隱藏、五裝置出現 | `test_segment_threshold_hides_four_devices_shows_five`（新增）、`test_segments_hide_districts_and_place_types_below_min_devices` |
| 90 天截止：只刪除更舊列 | `test_cleanup_main_commits_cutoff_and_closes`（cutoff 字面值 = 固定 now − 90 天）、`test_delete_expired_events_returns_cutoff_param_and_rowcount`（`occurred_at < %s` 參數化）、`test_cleanup_main_defaults_to_utc_now` |
| 拒絕同意：無 UUID、無分析請求 | `test_query_without_consent_never_writes_analytics`、`test_decline_removes_consent_and_uuid_without_sending_events`（新增） |
| 導航不被 beacon 失敗阻擋 | `test_navigation_uses_beacon_with_keepalive_fallback`（加強：點擊處理器區段無 `preventDefault`、`.catch(() => {})` 吞錯）、`test_navigation_capture_uses_single_delegated_handler` |

`static/app.js` 導航委派處理器不取消、不等待 beacon，`sendAnalyticsEvent` 失敗路徑僅 `.catch(() => {})`，故 Maps 連結行為不受分析影響（本機為靜態契約證據）。

## 5. 延遲次要事項分類（QA／最終審查分級）

依控制者提供的清單逐項分類，均不阻擋合併：

- **Task1 None-headers 守衛／粗略轉換**：`analytics_identity` 與 `coarse_area_bucket` 已處理 None；粗略網格是設計目標。→ 不需修。
- **Task2 更強導航 SQL 測試／狀態鍵**：導航 SQL 契約已有 `test_navigation_insert_requires_matching_recent_query`；狀態鍵形狀有測試。→ 已覆蓋。
- **Task3 瀏覽器事件建構安全包裝／不可達 fallback**：`write_analytics_safely` 已通用包裝；fallback 分支屬防禦性不可達。→ 保留防禦，不需修。
- **Task4 分群 parity／referrer／來源文字契約**：JS/Python `availability_bucket` 一致；無自由文字欄位，referrer 不持久化。→ 不需修。
- **Task5 失敗查詢的 provisional 旗標**：`navigation_provisional` 把 24 小時內的 query_failed 也算入，可能標「暫估」；僅影響標籤語氣，指標數字不受影響。→ 可延遲。
- **Task6 範圍競態／meminfo IndexError／多餘分析 pill**：範圍切換競態僅 UI 顯示順序；`_read_memory_percent` 對 `MemAvailable:` 空值行會漏接 `IndexError`（已用最小樣本確認），但 `/proc/meminfo` 格式由核心保證，屬防禦性次要；分析 pill 即使停用也顯示「未設定」是刻意設計。→ 可延遲（meminfo 修補列入 backlog）。
- **Task7 UA/referrer 測試與 /admin 精確強化**：admin no-store、noindex、405、Basic Auth、無 IP 日誌皆有合約測試；UA/referrer 不進入事件欄位。→ 已覆蓋。

## 6. 僅限 live VM 的檢查清單（Step 5）

以下項目無法在本機自動化，留待部署時於 VM 執行（本任務**未**執行）：

1. 在備份／可回滾的 VM 上套用 `migrations/20260823_add_analytics_events.sql`。
2. 建立僅存於 VM 的 HMAC 秘密（`openssl rand -hex 32` → `/opt/parking-hell/.env`）與 htpasswd（`sudo htpasswd -c /etc/nginx/.htpasswd-parking-radar admin`）。
3. reload 前先 `sudo nginx -t`。
4. 驗證 `/admin/` 無憑證回 401、有憑證回 200。
5. 驗證 cron 清理（90 天）與儀表板對真實資料的顯示。
6. 確認新存取日誌中不出現任何地址／IP。

## 7. 變更與提交

- `tests/test_analytics_routes.py`、`tests/test_admin_dashboard.py`、`tests/test_analytics_metrics.py`、`tests/test_frontend_contract.py`：驗收測試補強與新增（無生產程式碼變更）。
- `docs/QA_REVIEW_2026-08-23_ANALYTICS.md`（本報告）。
- `docs/superpowers/plans/2026-08-23-parking-analytics-dashboard.md`：既有實作計劃文件，唯一意圖中的未追蹤檔，併入 Task 8 文件提交。

最終提交：

- `fb36927` `test: close task 8 analytics acceptance gaps`
- `docs: add task 8 QA review and analytics plan`（HEAD，含本報告與計劃文件）

## 8. 最終驗證（Step 7，提交後乾淨 shell 重跑）

提交後於乾淨 shell 重跑完整 Step 1 指令集，輸出如下：

```text
285 passed in 2.08s
compileall: exit 0（靜默）
node --check static/app.js: exit 0
node --check static/admin_analytics.js: exit 0
```

最終狀態：所有檢查綠燈、分支已提交、未 merge／push／deploy。本任務未發現需要 RED→GREEN 生產修復的失敗。
