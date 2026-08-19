# 停車地獄雷達自用完整版：費率、假日、場站型態與 PWA 設計

日期：2026-08-19
狀態：待使用者確認

## 1. 目標

將目前的期中專題升級成日常自用工具，解決兩個問題：

1. 推薦結果缺少抵達時適用的汽車費率、當日上限與場站型態。
2. 每次使用都要尋找網址，手機入口不夠方便。

完成後，使用者可從手機桌面一鍵開啟 PWA，輸入目的地後，在首選、備選與不建議場站中直接看到抵達日類型、汽車時租、汽車當日上限與場站型態。

## 2. 範圍

### 本次包含

- 依抵達時間判斷平日、週末、國定假日與補班日。
- 從臺北市官方 `FareInfo` 與 `payex` 整理小型汽車每小時費率。
- 僅在可確認屬於小型汽車時，整理當日停車上限。
- 整合人工覆寫、官方名稱／說明與 OpenStreetMap，補充場站型態。
- 首選卡、備選與不建議列表顯示精簡費率與型態。
- 保留完整官方費率原文與營業時間。
- 將現有網站改成可安裝的 PWA。

### 本次不包含

- LINE Bot、LIFF 或原生 Android／iOS App。
- 推播通知、背景定位或抵達時空位預測。
- 付費 Google Maps／Places API。
- 自動爬取每一家民營停車場官網。
- 離線停車查詢；即時空位仍必須連線。

## 3. 使用者介面

### 首選卡

首選卡在空位資訊下方固定顯示：

```text
抵達：週六 18:00｜國定假日
汽車費率：60 元／時
當日上限：230 元
場站型態：地下停車場
```

無法判定唯一費率時顯示範圍與原因：

```text
汽車費率：40～60 元／時
依活動或現場公告
當日上限：官方未標示
```

「官方未標示」只表示來源未提供，不代表免費或沒有上限。

### 備選與不建議列表

精簡為單行資訊：

```text
60 元／時・上限 230 元・地下｜8 / 100 格
```

窄螢幕可以自然換行，但不得隱藏費率、上限或型態。

### 完整費率

原有「費率與營業時間」區塊繼續保留官方 `payex` 原文，讓使用者可核對複雜活動日、跨日與現場規則。

## 4. 假日與補班日

### 資料來源

使用 TaiwanCalendar 的年度 JSON：

```text
https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json
```

年度檔包含 `date`、`week`、`isHoliday` 與 `description`，可區分一般工作日、週末、國定假日及補班日。同步程式會保存目前年度與下一年度至 `data/calendar/`。

### 查詢規則

- 一律使用 API 已驗證、帶 Asia/Taipei 時區的 `arrival_time`。
- `isHoliday=true` 且有名稱：顯示「國定假日｜名稱」。
- `isHoliday=true` 且無名稱：顯示「週末」。
- 週六但 `isHoliday=false`：顯示「補班日」。
- 其他：顯示「平日」。

### 失敗降級

- 同步失敗但已有快取：沿用快取。
- 查詢年份沒有快取：只用星期判斷「平日／週末」，並回傳 `calendar_source=weekday_fallback`。
- 日曆同步不得發生在使用者查詢路徑。

## 5. 汽車費率與上限

### 儲存資料

collector 除了既有 `fee_info=payex`，再將官方 `FareInfo` 原始結構序列化至 `fare_rules_json`。保留原始資料，避免清洗後無法重新解讀。

### 每小時費率

`fee_service.py` 只接受小型汽車規則：

- `ParkingType` 為 `C` 或 `CM`。
- `RateType` 為 `1`（計時）。
- 依 `ChargeableSTime`、`ChargeableETime` 與抵達小時篩選，支援跨午夜時段。
- 唯一適用價格時回傳單一金額。
- 同一時間有多個合理價格、或 `payex` 明確表示依平假日／活動變動時，回傳最小至最大範圍及說明，不自行猜一個價格。
- 結構化規則缺漏時，才從 `payex` 的小型車計時段落擷取價格。

### 當日上限

- 只解析「小型車／汽車」段落中的「當日最高、每日最高、24 小時最高、上限」等明確文字。
- 遇到獨立「機車」段落後停止使用其中的上限數字。
- 月租、雙月票、計次費率不得當成每日上限。
- 無法確認時回傳 `null`，前端顯示「官方未標示」。

### 回傳欄位

每個場站新增：

- `arrival_day_label`
- `hourly_fee_label`
- `daily_cap_label`
- `fee_note`
- `fee_confidence`：`exact`、`range` 或 `unknown`

## 6. 場站型態

### 類型

- `mechanical`：機械式
- `surface`：平面式
- `underground`：地下停車場
- `multi_storey`：立體停車場
- `mixed`：混合型
- `unknown`：型態待確認

### 來源優先順序

1. `data/parking_overrides.json` 人工覆寫。
2. 臺北市官方名稱與說明中的明確關鍵字。
3. OpenStreetMap `amenity=parking` 的 `parking` 標籤。
4. `unknown`。

官方判斷只接受明確文字「機械、平面、地下、立體」；不得把地下或立體推論成機械式。

OSM 僅映射：

- `parking=surface` → 平面式
- `parking=underground` → 地下停車場
- `parking=multi-storey` → 立體停車場

OSM 不提供可靠機械式判斷。OSM 與官方場站以座標距離匹配，只有 40 公尺內且候選唯一時才採用；否則保持未知。人工覆寫以 `lot_id` 為鍵，永遠具有最高優先權。

### 更新方式

`parking_metadata.py --sync` 使用 Overpass API 批次取得臺北市 `amenity=parking`，寫入資料庫快取。此命令由獨立 systemd timer 每月執行，不得在 `/api/query` 中呼叫。

## 7. PWA 與自用入口

### 安裝

新增：

- `static/manifest.webmanifest`
- `static/sw.js`
- 192、512 與 maskable App 圖示
- HTML manifest、theme-color 與 Apple touch icon 標記

安裝後以 standalone 模式開啟，名稱為「停車地獄雷達」。Android 顯示安裝提示；iOS 顯示「分享 → 加入主畫面」說明。

### 快取策略

- HTML、CSS、JavaScript、圖示：版本化 static cache。
- 導航：network-first，失敗時使用已快取首頁殼層。
- `/api/*`：永遠 network-only，不快取即時空位回應。
- OpenStreetMap 圖磚與 Google Maps 連結：不由 service worker 快取。
- 新版部署後更新 cache 版本並刪除舊 cache。

## 8. 資料庫與遷移

`parking_lots` 新增：

```sql
fare_rules_json LONGTEXT NULL,
facility_type VARCHAR(20) NULL,
facility_source VARCHAR(20) NULL,
metadata_checked_at DATETIME NULL
```

新增可重複安全執行的 migration。現有資料不刪除，新增欄位皆允許 `NULL`，所以部署後即使日曆或 OSM 尚未同步，原本查詢仍可正常使用。

## 9. 程式結構

新增三個聚焦模組：

- `fee_service.py`：汽車計時、時段、費率範圍與上限解析。
- `calendar_service.py`：年度日曆同步、快取與抵達日分類。
- `parking_metadata.py`：人工覆寫、官方關鍵字、OSM 同步與來源優先順序。

既有檔案只做必要串接：

- `collector.py` 保存 `FareInfo`。
- `database.py` 寫入與讀取新增欄位。
- `app.py` 將抵達時間交給費率與日曆服務，輸出新欄位。
- `static/app.js` 顯示完整卡與精簡列表，處理 PWA 安裝提示。
- `templates/index.html` 加入 PWA 標記與安裝提示區塊。
- `static/style.css` 加入費率列與安裝提示樣式。

## 10. 資料流與效能

背景資料流：

```text
臺北市 API → collector → MySQL（停車場、FareInfo、空位快照）
年度日曆 → calendar_service → data/calendar/*.json
Overpass API → parking_metadata → MySQL（型態與來源）
人工確認 → parking_overrides.json ───────────────┘
```

使用者查詢：

```text
PWA → Flask → 本機日曆＋MySQL → 費率解析＋原推薦規則 → JSON → 圖卡
```

使用者查詢不得呼叫日曆下載或 Overpass。正常查詢增加的工作只有本機 JSON 查找與字串解析，目標是不使 API 回應時間增加超過 100 毫秒。

## 11. 錯誤處理

- 日曆、OSM 或人工覆寫資料錯誤不得中止停車推薦。
- `fare_rules_json` 格式不合法時退回 `payex`；仍無法解析則顯示官方未標示。
- OSM 候選不唯一時不得猜測。
- service worker 不攔截即時 API，不得顯示過期空位為即時結果。
- 所有未知值在 JSON 使用 `null` 或明確 `unknown`，前端統一轉為白話文字。

## 12. 測試與驗收

### 自動測試

- 國定假日、週末、一般平日與補班日。
- 跨午夜時段與多費率範圍。
- 小型車費率，不混入機車、月租或計次價格。
- 汽車每日上限，不混入機車上限。
- 人工覆寫 > 官方明確文字 > OSM > 未知。
- OSM 距離超過 40 公尺或候選不唯一時保持未知。
- API 新欄位與舊欄位相容。
- 首選、備選與不建議列表皆包含費率、上限與型態。
- service worker 明確排除 `/api/`。
- 原有測試全數通過。

### 線上驗收

1. 查詢台北車站，三張首選與其他場站皆顯示新增資訊。
2. 使用平日、週末、國定假日與補班日時間各查一次。
3. 驗證至少一座有汽車上限、一座無上限資料及一座複雜費率。
4. 安裝至 Android／iOS 主畫面，從圖示可獨立開啟。
5. 關閉網路時可以開啟應用殼層，但查詢明確顯示需要網路。
6. 日曆與 Overpass 模擬失敗時，原本即時停車推薦仍正常。

## 13. 部署

1. 備份 `parking_lots` 與目前程式。
2. 執行 migration。
3. 同步目前與下一年度日曆。
4. 執行一次場站型態同步。
5. 部署後端與 PWA 靜態檔。
6. 重啟 Gunicorn，確認 `/health`。
7. 以台北車站完成桌面與手機 QA。
8. 啟用每月 metadata timer；其失敗不得影響主服務。

## 14. 成功標準

- 手機桌面可一鍵開啟停車地獄雷達。
- 使用者不展開詳細資料，也能看到抵達日、汽車時租、汽車上限與場站型態。
- 未知或複雜資料不產生假精確結果。
- 首選與所有其他場站顯示規則一致。
- 外部增強來源失敗時，原有推薦仍可使用。
- 查詢時不新增日曆或 OSM 網路等待。
