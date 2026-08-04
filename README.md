# 停車地獄雷達

整合臺北市官方停車資料，以固定公式分析即時難度、歷史參考、距離與推薦；Gemini 只解析限定意圖。

## 功能範圍

- 臺北市路外停車場、地址 1.5 公里搜尋、三名推薦、避雷、平日／週末歷史參考。
- 單頁 Leaflet 地圖與一張最近七天折線圖。
- 不含導航、AI 空位預測、路邊格位、會員及個別民營業者爬蟲。

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

## GCP 1 vCPU／1 GB 部署

1. 建立 Ubuntu VM，執行 `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`，並在 `/etc/fstab` 加入 `/swapfile none swap sw 0 0`。
2. 安裝 Python、MySQL、Nginx，建立 `parking` 系統使用者，專案放在 `/opt/parking-hell` 並由該使用者擁有。
3. 在 MySQL 設定加入：`bind-address=127.0.0.1`、`innodb_buffer_pool_size=128M`、`max_connections=30`、`performance_schema=OFF`，重新啟動後確認 3306 未對外開放。
4. 建立 `.venv` 與不進 Git 的 `.env`，執行 schema 與第一次 collector。
5. 安裝 `deploy/parking-radar.service` 與 `deploy/nginx-parking-radar.conf`。
6. 以 `systemctl enable --now parking-radar nginx` 啟動。
7. 以 `parking` 使用者執行 `crontab -e` 加入：

```cron
*/30 * * * * cd /opt/parking-hell && /opt/parking-hell/.venv/bin/python collector.py --once >> /opt/parking-hell/collector.log 2>&1
```

## 展示檢查

1. 手動選行政區可完成查詢。
2. 輸入臺北市地址可顯示 1.5 公里候選、最近與前三名推薦。
3. Gemini 可處理推薦、歷史、平週末比較及一次簡單追問。
4. 關閉 Gemini 金鑰後，頁面會引導手動查詢。
5. Nominatim 查不到地址時，可退回行政區查詢。
6. 地圖、唯一折線圖、資料時間與 OpenStreetMap 標示皆可見。
