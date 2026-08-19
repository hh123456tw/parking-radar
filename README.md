# 停車地獄雷達

整合臺北市官方停車資料，以固定公式分析即時難度、歷史參考、距離與推薦；Gemini 只解析限定意圖。

## 功能範圍

- 臺北市路外停車場、地址 1.5 公里搜尋、三名推薦、避雷、平日／週末歷史參考。
- 模糊地標可產生並驗證最多三個候選，使用者能在前端直接選擇。
- 單頁 Leaflet 地圖與一張最近七天折線圖。
- 不含導航、AI 空位預測、路邊格位、會員及個別民營業者爬蟲。

重要行為變更整理於 [CHANGELOG.md](CHANGELOG.md)。

## Windows 本機啟動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
mysql -u root -p -e "CREATE DATABASE parking_hell CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
Get-Content -Raw schema.sql | mysql -u root -p parking_hell
python collector.py --once
python -m pytest -q
flask --app app run --debug
```

若要在 Windows 每 15 分鐘自動收集一次，可於 PowerShell 以系統管理員註冊工作排程器：

```powershell
$projectRoot = (Resolve-Path .).Path
$action = New-ScheduledTaskAction `
  -Execute "$projectRoot\.venv\Scripts\python.exe" `
  -Argument "collector.py --once" `
  -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger `
  -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask `
  -TaskName "ParkingRadarCollector" `
  -Action $action -Trigger $trigger `
  -Description "每 15 分鐘收集臺北市停車快照"
```

## 環境變數

| 名稱 | 用途 |
|---|---|
| `FLASK_SECRET_KEY` | Flask session 簽章；部署時必須換成長隨機字串 |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 位址，部署時固定 localhost:3306 |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 專題資料庫帳號與名稱 |
| `GEMINI_API_KEY` | 留空時停用對話並使用手動表單 |
| `GEMINI_MODEL` | 預設 `gemini-3.5-flash-lite` |
| `NOMINATIM_USER_AGENT` | 必須包含可辨識的專題名稱與聯絡資訊 |

對話只說目的地而未指定抵達時間時，系統會自動使用 `Asia/Taipei` 的目前時間。

## 計算與資料清洗

- 停車場地獄指數：`(總車位 - 剩餘車位) / 總車位 × 100`。
- 行政區地獄指數：`全區已使用有效車位 / 全區有效總車位 × 100`。
- 有歷史樣本：即時容易度 50% + 距離容易度 30% + 歷史容易度 20%。
- 歷史不足：即時容易度 60% + 距離容易度 40%。
- `-9`、`-11`、`-12`、`-13` 是官方特殊狀態，不是負車位，不進入數值計算。
- 歷史分析為過去樣本參考，不代表抵達時仍有相同空位。
- 圖卡的推薦、警示、避雷與白話原因全部由 Python 固定規則產生；Gemini 只解析對話條件。
- 停車場地址與 Google 地圖連結使用既有座標或地址，不使用付費 Google Maps API。
- 頁面分開顯示官方動態資料時間與本系統抓取時間，避免誤判資料新鮮度。

## GCP 1 vCPU／1 GB 部署

1. 建立 Ubuntu VM，執行 `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`，並在 `/etc/fstab` 加入 `/swapfile none swap sw 0 0`。
2. 安裝 Python、MySQL、Nginx，建立 `parking` 系統使用者，專案放在 `/opt/parking-hell` 並由該使用者擁有。
3. 在 MySQL 設定加入：`bind-address=127.0.0.1`、`innodb_buffer_pool_size=128M`、`max_connections=30`、`performance_schema=OFF`，重新啟動後確認 3306 未對外開放。
4. 建立 `.venv` 與不進 Git 的 `.env`，執行 schema 與第一次 collector。
5. 安裝 `deploy/parking-radar.service` 與 `deploy/nginx-parking-radar.conf`；其中 nginx 必須為 `location /static/` 加上 `Service-Worker-Allowed: /` 回應標頭，否則 PWA 服務工人無法以 `/` 範圍註冊（見下方「部署補充」）。
6. 以 `systemctl enable --now parking-radar nginx` 啟動。
7. 以 `parking` 使用者執行 `crontab -e` 加入：

```cron
*/15 * * * * cd /opt/parking-hell && /opt/parking-hell/.venv/bin/python collector.py --once >> /opt/parking-hell/collector.log 2>&1
```

### 部署補充：行事曆、費率與設施中繼資料

首次部署或升級既有部署時，依序完成以下步驟：

1. 備份 `parking_lots`：
   `mysqldump -u parking -p parking_hell parking_lots > parking_lots.backup.sql`
2. 套用中繼資料遷移（重複執行安全）：
   `mysql -u parking -p parking_hell < migrations/20260819_add_parking_metadata.sql`
3. 收集一次資料：
   `/opt/parking-hell/.venv/bin/python collector.py --once`
4. 下載行事曆：
   `/opt/parking-hell/.venv/bin/python calendar_service.py --sync`
5. 同步費用與設施型態：
   `/opt/parking-hell/.venv/bin/python parking_metadata.py --sync`
6. 安裝並啟用每月維護計時器：
   `sudo install -m 644 deploy/parking-metadata-refresh.service deploy/parking-metadata-refresh.timer /etc/systemd/system/`
   `sudo systemctl daemon-reload && sudo systemctl enable --now parking-metadata-refresh.timer`
7. 重啟 Gunicorn 並確認健康檢查：
   `sudo systemctl restart parking-radar && curl -fsS http://127.0.0.1:8000/health`
8. 查詢「台北車站」，確認主要與精簡卡片、地圖與七天歷史行為皆正常。
9. 安裝 PWA：Android Chrome 使用「安裝應用程式」；iOS Safari 使用「加入主畫面」。

計時器於每月執行一次（`OnCalendar=monthly`），補行錯過批次（`Persistent=true`），並附加一小時隨機延遲
（`RandomizedDelaySec=1h`），避免與資料來源尖峰重疊。維護任務是獨立的 `Type=oneshot` 單位，其成敗不會重啟或停止 `parking-radar.service`。

PWA 離線外殼需要 nginx 對 `sw.js` 回應 `Service-Worker-Allowed: /`。前端以 `scope: "/"` 註冊（見 `static/app.js`），若 nginx 未在 `location /static/` 傳回該標頭，瀏覽器會拒絕超出
`/static/` 的範圍，服務工人便無法控制並離線快取整站。`deploy/nginx-parking-radar.conf` 對應區塊需為：

```nginx
location /static/ {
    alias /opt/parking-hell/static/;
    expires 1h;
    add_header Service-Worker-Allowed /;
}
```

### 資料來源與授權

- 官方停車資料依臺北市開放資料授權，可商業再利用，但須標示資料來源為臺北市政府。
- 地圖與地標資料使用 OpenStreetMap；OSM 標示在頁面保持可見。
- 系統無法判斷時以明確「未知」標籤呈現，不代表實際收費或設施型態的保證。
- 本系統不含會員帳號，也不儲存個人目的地歷史；所有查詢一律匿名。

## 展示檢查

1. 手動選行政區可完成查詢。
2. 輸入臺北市地址可顯示 1.5 公里候選、最近與前三名推薦。
3. Gemini 可處理推薦、歷史、平週末比較及一次簡單追問。
4. 關閉 Gemini 金鑰後，頁面會引導手動查詢。
5. Nominatim 查不到地址時，可退回行政區查詢。
6. 地圖、唯一折線圖、資料時間與 OpenStreetMap 標示皆可見。
