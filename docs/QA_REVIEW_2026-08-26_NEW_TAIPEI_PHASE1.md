# QA Review — 2026-08-26 新北第一階段（遷移排練、全量 QA 與發布閘）

- 日期：2026-08-26（台北時區）
- 分支工作樹：`.worktrees/new-taipei-phase1`，base commit `7a245a1`
- 結果：**通過**。所有離線檢查綠燈；遷移在一次性 MySQL 上排練兩輪且資料不變；旗標關閉／開啟的離線 API smoke 全數通過；未部署、未合併、未推送、未觸碰任何 VM 或既有資料庫。
- 最終測試數：**454 passed**；關鍵子套件 136 passed；離線 smoke **29/29 passed**；靜態檢查全部 exit 0。

## 1. 依賴安裝與全量自動測試

依 brief 執行：

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

結果：

- `pip install`：全部 requirements 已滿足，exit 0。
- `pytest`：**454 passed in 2.29s**，無 skip、無 xfail、無警告。

本機環境：Python 3.11.9、Node v24.13.0（CI 使用 Python 3.13／Node 22，離線契約由 `tests/test_ci_contract.py` 鎖定）。

## 2. 靜態驗證

依 brief 執行（含 repository-wide compileall，`parking_cleanup.py` 與 `new_taipei_source.py` 均納入）：

```bash
python -m compileall -q .
node --check static/app.js
node --check static/admin_analytics.js
git diff --check
```

另執行 CI 同款 `node --check static/sw.js`。

| 指令 | 結果 |
| --- | --- |
| `python -m compileall -q .` | exit 0，靜默 |
| `node --check static/app.js` | exit 0 |
| `node --check static/admin_analytics.js` | exit 0 |
| `node --check static/sw.js` | exit 0 |
| `git diff --check` | exit 0（無空白錯誤） |

## 3. 遷移排練（一次性 MySQL，兩輪）

### 3.1 環境與目標解析

- 本機既有 `MySQL80` 服務（127.0.0.1:3306）以 root 空密碼連線被拒（`ERROR 1045 Access denied`），且未取得任何憑證；**未使用、未修改、未刪除該服務上的任何資料庫**。
- 改以 Docker Desktop 引擎建立**一次性** `mysql:8` 容器（伺服器版本 **8.4.11**）。容器名 `parking-hell-qa-mysql-20260826-e60x6z6w`，**不發布 host port**，連線僅經 `docker exec` 進入容器內執行。
- 建立資料庫前先以 `SHOW DATABASES` 確認目標不存在；只建立兩個明確命名的 QA 資料庫：
  - `ph_qa_ntp_upgrade_20260826_e60x6z6w`（升級路徑）
  - `ph_qa_ntp_fresh_20260826_e60x6z6w`（全新安裝路徑）
- 密碼為一次性隨機值，僅存在於排練程序的記憶體，未寫入任何檔案或文件。

### 3.2 Rehearsal A：既有安裝升級路徑（migration 重點）

以 git 保留的 migration 前 schema（`a72b29b^:schema.sql`，無 `city/source/source_lot_id` 欄位）建立結構，插入 3 筆既有臺北場站與 4 筆快照，再執行 `migrations/20260826_add_parking_sources.sql` 兩次。

| 檢查 | 第 1 次執行 | 第 2 次執行 |
| --- | --- | --- |
| `parking_lots` 筆數 | 3 → 3（不變） | 3（不變） |
| `parking_snapshots` 筆數 | 4 → 4（不變） | 4（不變） |
| 全部列 backfill（`city=臺北市`、`source=taipei`、`source_lot_id=lot_id`） | 3/3 | 3/3 |
| `city/source/source_lot_id` NOT NULL 欄位數 | 3 | 3 |
| `uq_lots_source_id`（distinct index 數） | 1（由 2 欄組成：source, source_lot_id） | 1（由 2 欄組成） |
| SQL 錯誤 | 無 | 無 |

逐列驗證（第 2 次執行後）：

```text
lot_id   city    source   source_lot_id
TPE0001  臺北市   taipei   TPE0001
TPE0002  臺北市   taipei   TPE0002
TPE0003  臺北市   taipei   TPE0003
```

### 3.3 Rehearsal B：全新安裝路徑

以目前 `schema.sql`（已含新欄位）建立結構，先插入 1 筆新北場站（`新北市/new_taipei/010056`），再執行 migration 兩次：

| 檢查 | 第 1 次執行 | 第 2 次執行 |
| --- | --- | --- |
| `parking_lots` 筆數 | 1 → 1（不變） | 1（不變） |
| 原列值保持（`新北市/new_taipei/010056`） | 1/1 | 1/1 |
| `uq_lots_source_id`（distinct index 數） | 1 | 1 |

唯一鍵功能驗證：以相同 `(source='new_taipei', source_lot_id='010056')` 但不同 `lot_id` 插入，被 MySQL 以 `Duplicate entry` 拒絕（rc=1）。

### 3.4 清理

- `DROP DATABASE` 僅針對上述兩個 QA 資料庫；執行後 `SHOW DATABASES` 確認 `ph_qa_ntp_*` 殘留為 **0**。
- `docker rm -f` 移除一次性容器；本機既有容器與 `MySQL80` 服務保持原狀。

## 4. 離線 API smoke（旗標關閉與開啟）

方法：Flask test client + 固定 fixtures/mocks；`geocode_address`、`geocode_candidates`、Gemini `parse_parking_query`、DB 連線、`fetch_current_lots`、`fetch_history`、`parking_data_status` 全部以固定資料替換；`OPENROUTESERVICE_API_KEY` 留空、`AUTO_REFRESH_ENABLED=False`、Analytics 關閉。**全程未呼叫真實 Gemini、Nominatim、臺北／新北、OpenRouteService 或其他外部 API。**

### 4.1 `NEW_TAIPEI_ENABLED=0`（13 項）

| 驗收項目 | 結果 |
| --- | --- |
| `/` 渲染（含「停車地獄雷達」） | 200，PASS |
| 注入城市選項僅 `["taipei"]` | PASS |
| 頁面不含「新北市」 | PASS |
| `/health` 回傳 `{"status":"ok"}` | PASS |
| 臺北手動查詢「台北車站」 | 200，PASS |
| 既有排序不回退（`NEAR` 在 `FAR` 之前） | `['NEAR','FAR']`，PASS |
| 全部結果 `city=臺北市`、`source=taipei` | PASS |
| 資料時間標籤 `臺北市官方資料時間 ...` | PASS |
| `data_sources` 只有 taipei 且 `fresh`、`time_kind=official` | PASS |
| 費率欄位（`hourly_fee_label=60 元／時`，`fee_info` 原文保留） | PASS |
| 聊天模式 Gemini 失敗 → 503 + `fallback=manual` | PASS |
| 歷史 API（真實 `build_history_series` + +08:00 轉換） | PASS |
| 旗標關閉時 `city=new_taipei` 一律 400（即使資料庫殘留新北列） | PASS |

### 4.2 `NEW_TAIPEI_ENABLED=1`（16 項）

| 驗收項目 | 結果 |
| --- | --- |
| 注入城市選項 `["taipei","new_taipei"]`，頁面含「新北市」 | PASS |
| 「板橋車站」查詢 | 200；回傳 `{臺北市, 新北市}` 兩市場站，PASS |
| 新北場站標籤 `新北市系統取得時間 ...` | PASS |
| 每來源 metadata：taipei `fresh/official`、new_taipei `fresh/collected` | PASS |
| 「新北市政府」不會被解析成「臺北市政府」 | PASS（destination 為「新北市政府, 板橋區, 新北市」） |
| 「新北市政府」查詢回傳新北場站 | PASS |
| 跨市地址（板橋車站，1.5 km 圓跨行政邊界）同時回傳雙市場站 | PASS |
| Google Maps 導航 URL 契約（`google.com/maps/dir/?api=1&travelmode=driving&destination=` + encode） | PASS |
| 每個候選輸出數值 `latitude/longitude`（導航與地圖標記所需） | PASS |
| Leaflet 目的地標記與候選 `circleMarker([lot.latitude, lot.longitude])` 契約 | PASS |
| 新北 fixture 費率：原文 `小型車計時60元;` 保留；無法明確解析時顯示「請查看官方費率」（不猜測） | PASS |
| 新北場站歷史 API（+08:00 轉換） | PASS |
| 過期來源行為：taipei `fresh`、new_taipei `stale`（120 分鐘）並列顯示 | PASS |
| `data_status=stale`，notice 含「新北市資料120 分鐘前」 | PASS |
| 過期來源的舊列仍回傳並誠實標示系統取得時間 | PASS |
| 新鮮來源維持 45 分鐘門檻、過期來源不加門檻（`freshness_minutes=45` vs `None`） | PASS |

合計 **29/29 PASS**。

## 5. 資料品質計數（固定 fixture 測量）

以 `tests/fixtures/new_taipei_*.json` 離線測量：

- 靜態 fixture：2 列 → 2 場站；有效 WGS84 座標 **2/2**，無效座標 **0**。TWD97 已知點轉換在容差內（`296882, 2767068` → `25.0109252, 121.4644919`）。
- 座標防護：缺值、非數字、超出雙北範圍的 TWD97 一律回傳 `(None, None)`（探測 `(121.0, 25.0)` → `(None, None)`）。
- 動態 fixture：3 列 → 2 筆有效快照（`NTP:010056`、`NTP:999999`）；負數 `-9` 跳過（`invalid_dynamic=1`）。
- 對應不到靜態場站的動態 ID：`999999` 被排除（`unmatched_dynamic=1`）。
- 含重複靜態列的 adapter 測量：`duplicates=1`、`invalid_dynamic=1`、`unmatched_dynamic=1`，重複 ID 最後一筆勝出。

## 6. 限制與誠實聲明

- 本機 Python 為 3.11.9（CI 鎖定 3.13）；Node 24.13.0（CI 鎖定 22）。離線測試與語法檢查均通過，未在 3.13 上實際執行。
- 遷移排練使用一次性 Docker `mysql:8`（8.4.11）容器；生產目標為 MySQL 8.x。排練驗證 additive／idempotent SQL 與資料保留，不包含真實 VM 上的服務切換或備份還原。
- 本機既有 `MySQL80` 服務因無可用憑證未使用；未建立、未刪除、未修改其中任何資料庫。
- smoke 測試全部使用 fixtures/mocks，數字不代表真實新北 API 的場站數量與內容；開啟旗標前仍需依計畫以真實資料驗證場站數、座標、行政區、費率與有效快照比例。
- 費率「小型車計時60元;」無法由現有解析規則明確判斷，前端顯示「請查看官方費率」並保留官方原文，符合「不猜測費率」的設計原則。

## 7. 回滾指示

1. **程式回滾**：設定 `NEW_TAIPEI_ENABLED=0` 並重啟服務（`systemctl restart parking-radar` 或等同指令）。旗標關閉後：前端不顯示新北市、collector 不收集新北、查詢與聊天一律拒絕新北目的地，既有臺北行為不變。
2. **資料庫**：本次 migration 為 additive 且可重複執行。回滾程式時**不得移除** `parking_lots.city`、`source`、`source_lot_id` 欄位或 `uq_lots_source_id` 唯一鍵（移除會破壞既有資料列）；新北資料可保留，重新開啟旗標即可恢復。
3. **快照清理（Task 8 既有功能）**：停用 `parking-snapshot-cleanup.timer` 並還原上一版 `parking_cleanup.py`／`database.py`；該功能不變更 schema，已刪除的 8 天前快照只能從備份還原。

## 8. 提交內容

- `README.md`：Documentation 區新增本 QA 文件連結。
- `docs/QA_REVIEW_2026-08-26_NEW_TAIPEI_PHASE1.md`：本文件。
- Commit：`docs: record New Taipei phase one QA`
- 已確認 diff 僅包含上述兩個檔案，無任何 runtime 程式碼或測試變更。
