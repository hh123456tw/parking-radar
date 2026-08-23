# QA Review — 2026-08-23 匿名分析儀表板（Task 8 全功能隱私、迴歸與品質閘）

- 日期：2026-08-24（台北時區）
- 分支工作樹：`.worktrees/parking-analytics-dashboard`
- 結果：**通過**。所有離線檢查綠燈；隱私/秘密掃描逐項檢視無外洩；本機自動化驗收全數通過；無部署、無合併、無推送。
- 最終測試數：**298 passed**（Task 8 前基線 280 + 6 個驗收測試 + 最終修補波新增 12 個測試，見第 9 節）。

## 1. 離線自動化檢查（Step 1）

在乾淨 shell 依序執行：

```bash
python -m pytest -q
python -m compileall -q .
node --check static/app.js
node --check static/admin_analytics.js
```

結果：

- `pytest`：`286 passed in 2.04s`（最終驗證重跑，見第 8 節）。
- `compileall`：靜默，exit 0，無任何警告。
- `node --check static/app.js`：exit 0。
- `node --check static/admin_analytics.js`：exit 0。

無警告、無 skip、無 xfail。CI 合約測試（`tests/test_ci_contract.py`）同時確認 push/PR workflow 內含 pytest、compileall（含分析四模組）與 Node 檢查（含 admin_analytics.js）。

### 複審修補：CI 涵蓋分析功能（Round 1/5，Important）

複審發現：GitHub Actions 的 compileall 清單未包含 `analytics_service.py`、`analytics_database.py`、`status_service.py`、`analytics_cleanup.py`，Node 檢查亦缺 `static/admin_analytics.js`——整個功能對 CI 盲區。

TDD RED：先新增 `tests/test_ci_contract.py::test_ci_compiles_analytics_modules_and_checks_admin_js`，要求 compileall 那一行含四個模組、JavaScript 步驟含 `node --check static/admin_analytics.js`。修補前執行：

```text
1 failed, 1 passed in 0.06s
AssertionError: assert 'analytics_service.py' in '        run: python -m compileall -q app.py ai_service.py analysis.py ...'
```

GREEN：最小修改 `.github/workflows/ci.yml`（compileall 行補上四模組、JS 步驟補上 `node --check static/admin_analytics.js`；保留 Python 3.13／Node 22／離線測試，無部署、無 secrets）。修補後執行：

```text
tests/test_ci_contract.py: 2 passed
python -m pytest -q: 286 passed in 2.04s
python -m compileall -q .: exit 0（靜默）
CI 同款 compileall 指令：exit 0（靜默）
node --check static/app.js: exit 0
node --check static/admin_analytics.js: exit 0
node --check static/sw.js: exit 0
```

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

結論：無硬編碼/個人秘密、無個人路徑、無 IP 日誌 token、無自由文字分析持久化、管理端程式碼不渲染任何目的地址或原始輸入。提交後重掃的少數命中是報告／計劃文件自身的良性自我引用，非真實秘密。

## 3. 迴歸：分析寫入失敗不得破壞停車查詢（Step 3）

既有測試 `tests/test_analytics_routes.py::test_query_survives_writer_exception`（Task 3 加入）已證明 `/api/query` 在 writer 拋錯時仍回 200 且回應相同（`recommendations[0]["lot_id"] == "TPE1"`）。本任務發現其缺口：只斷言 `"analytics_write_failed" in caplog.text`，未證明「只記錄固定 event_type、不含輸入目的地」。已**加強既有測試而非複製**，新增斷言：

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

最終修補波（見第 9 節）已處理原清單中可在本波收斂的項目：精簡導航 rank 0、place_type 診斷移除、
declined 持久化、失敗事件耗時、lot_id 長度、安全日誌、同意文案、`ANALYTICS_ENABLED` 文件、
provisional 旗標、meminfo IndexError、Nginx referrer/UA 與精確 `/admin`、QA/計劃文件潤飾。

仍明確延遲（範圍較大、不擴張架構，均不阻擋合併）：

- **儀表板範圍點擊競態**：快速切換 today/7d/30d 時，舊回應可能覆蓋新回應；僅影響 UI 顯示順序，
  需要請求序號或 AbortController 才能完整解決。
- **畸形 referrer／localStorage 防護**：`analyticsSource` 對 `document.referrer` 解析與
  localStorage 被停用／拋錯的情境屬防禦性強化，目前無實際故障樣本。
- **來源文字測試慣例**：前端契約測試沿用本倉庫既有的 source-text 慣例，非執行期 DOM 測試。
- **不可達 Gemini fallback**：`degraded_gemini_fallback` 在目前請求內沒有可用備援時不可達，
  屬防禦性保留。

## 6. 僅限 live VM 的檢查清單（Step 5）

以下項目無法在本機自動化，留待部署時於 VM 執行（本任務**未**執行）：

1. 在備份／可回滾的 VM 上套用 `migrations/20260823_add_analytics_events.sql`。
2. 建立僅存於 VM 的 HMAC 秘密（`openssl rand -hex 32` → `/opt/parking-hell/.env`）與 htpasswd（`sudo htpasswd -c /etc/nginx/.htpasswd-parking-radar admin`）。
3. reload 前先 `sudo nginx -t`。
4. 驗證 `/admin/` 無憑證回 401、有憑證回 200。
5. 驗證 cron 清理（90 天）與儀表板對真實資料的顯示。
6. 確認新存取日誌中不出現任何地址／IP。
7. 手動操作同意橫幅：接受後重新載入不再出現橫幅，頁尾「更改分析選擇」可重開選擇；
   拒絕後重新載入也不出現橫幅，瀏覽器保留 `declined` 選擇且不送出任何分析請求。

## 7. 變更與提交

- `tests/test_analytics_routes.py`、`tests/test_admin_dashboard.py`、`tests/test_analytics_metrics.py`、`tests/test_frontend_contract.py`：驗收測試補強與新增（無生產程式碼變更）。
- `.github/workflows/ci.yml`、`tests/test_ci_contract.py`：複審修補，CI 現在編譯分析四模組並 node-check `admin_analytics.js`。
- `docs/QA_REVIEW_2026-08-23_ANALYTICS.md`（本報告）。
- `docs/superpowers/plans/2026-08-23-parking-analytics-dashboard.md`：既有實作計劃文件，唯一意圖中的未追蹤檔，併入 Task 8 文件提交。

最終提交：

- `fb36927` `test: close task 8 analytics acceptance gaps`
- `docs: add task 8 QA review and analytics plan`（HEAD，含本報告與計劃文件）
- 複審修補提交（CI 涵蓋分析功能）

## 8. 最終驗證（Step 7，提交後乾淨 shell 重跑）

提交後於乾淨 shell 重跑完整 Step 1 指令集，輸出如下：

```text
286 passed in 2.04s
compileall: exit 0（靜默）
node --check static/app.js: exit 0
node --check static/admin_analytics.js: exit 0
```

最終狀態：所有檢查綠燈、分支已提交、未 merge／push／deploy。複審修補（CI 涵蓋）以 RED→GREEN 完成，紀錄見第 1 節；控制者指定的最終修補波見第 9 節。

## 9. 最終修補波（控制者指定，RED→GREEN）

控制者在本波交付 12 項修補（3 Important + 9 低風險 Minor），全部先寫測試、確認 RED 後才改生產程式碼。
修補波前基線 `0d029a5`：`286 passed`；修補波後：`298 passed`（新增/改寫 12 個測試）。

| # | 修補 | RED 證據 | GREEN 證據 |
| --- | --- | --- | --- |
| 1 | 精簡導航不得偽裝首選名次 | `test_compact_navigation_links_use_rank_zero`、`test_navigation_event_accepts_rank_zero_but_rejects_negative`、`test_rank_zero_clicks_count_in_rate_but_not_rank_shares` 先失敗 | `app.js` compact 連結固定 `data-navigation-rank="0"`；端點接受 0-99；指標把 rank 0 計入點擊率但不列入 1/2/3 占比 |
| 2 | 移除永遠為空的 place_type 診斷 | `test_admin_dashboard_omits_place_type_diagnostic` 先失敗 | 移除 admin HTML/JS 的「熱門地點類型」表；schema/summary 欄位保留向後相容；README 記錄延後原因 |
| 3 | declined 選擇持久化 | `test_decline_persists_choice_and_removes_uuid_without_sending_events`、`test_consent_banner_shows_only_when_no_choice_exists` 先失敗 | decline 寫入 `"declined"` 並刪 UUID；橫幅只在無任何選擇時顯示；footer 重開選擇不變 |
| 4 | 失敗終端事件記錄實際耗時 | `test_terminal_events_use_elapsed_helper_for_duration` 先失敗（`elapsed_ms` 不存在） | 新增 `elapsed_ms` helper，5 條終端路徑（成功＋4 種失敗）統一使用 |
| 5 | 拒絕 >32 字元 lot_id | `test_navigation_event_rejects_parking_lot_id_over_32_chars` 先失敗（33 字元原回 204） | 端點在 33 字元回 400、32 字元回 204 |
| 6 | 畸形事件安全日誌 | `test_write_analytics_safely_tolerates_missing_event_type` 先失敗（KeyError） | `event.get("event_type", "unknown")`，缺 event_type 只記 `event=unknown` |
| 7 | 同意/隱私文案揭露行政區與粗略區域 | `test_consent_banner_copy_is_exact`、`test_footer_offers_privacy_note_and_change_choice` 先失敗 | 橫幅與頁尾明示「行政區與約 1 公里見方的粗略區域」「不保存完整地址」 |
| 8 | 文件化 `ANALYTICS_ENABLED` | `test_example_env_has_names_but_no_real_secrets` 先失敗（缺 `ANALYTICS_ENABLED=1`） | `.env.example` 新增預設 `ANALYTICS_ENABLED=1`；README 環境變數表加一行 |
| 9 | provisional 只考慮合格完成查詢 | `test_recent_failed_or_resultless_query_does_not_mark_provisional` 先失敗（舊行為標暫估） | 改用 `eligible`（有結果的 `query_completed`）判斷 |
| 10 | meminfo IndexError 降級 | `test_linux_status_meminfo_empty_token_is_gray` 先失敗（IndexError） | `_read_memory_percent` 例外清單加入 `IndexError` |
| 11 | Nginx 隱私契約強化 | `test_access_log_format_omits_referrer_and_user_agent`、`test_exact_admin_path_redirects_into_protected_prefix`（後者先失敗） | 日誌格式合約鎖定不含 `$http_referer`/`$http_user_agent`；新增 `location = /admin { return 301 /admin/; }` |
| 12 | QA/計劃文件潤飾 | 文字修訂（無測試） | Task 3 歸屬修正；計劃檔個人絕對路徑移除；掃描自我引用註明；live 清單新增同意橫幅手動操作 |

本波受影響檔案：`app.py`、`analytics_service.py`、`status_service.py`、`static/app.js`、
`static/admin_analytics.js`、`templates/index.html`、`templates/admin_analytics.html`、
`deploy/nginx-parking-radar.conf`、`.env.example`、`README.md`、`docs/QA_REVIEW_2026-08-23_ANALYTICS.md`、
`docs/superpowers/plans/2026-08-23-parking-analytics-dashboard.md`，與 5 個測試檔。

最終驗證（本波提交後重跑）：

```text
python -m pytest -q: 298 passed in 2.04s
python -m compileall -q .: exit 0（靜默）
node --check static/app.js: exit 0
node --check static/admin_analytics.js: exit 0
node --check static/sw.js: exit 0
```

隱私/秘密掃描重跑：`$http_referer`／`$http_user_agent` 零命中；`$binary_remote_addr` 只存在於
`limit_req_zone`（記憶體限流鍵，不入日誌）；`X-Forwarded-For` 僅 proxy 轉發；`C:/Users` 個人路徑零命中
（計劃檔已改寫為不帶個人路徑的規格名稱）；`ANALYTICS_HMAC_SECRET` 命中皆為 README 部署指令與測試佔位
這類良性自我引用。
