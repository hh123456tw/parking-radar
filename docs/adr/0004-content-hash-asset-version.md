# ADR 0004：前端資源以內容雜湊作為版本號

- 狀態：採用
- 日期：2026-09-28

## 背景

PWA 的 Service Worker 對靜態檔採快取優先。原本版本字串（`decision-ui-v3`）要在模板、Service Worker 與 JavaScript 共 6 處手動同步；漏改一處，已安裝到主畫面的使用者就會一直執行舊版 JavaScript。

## 決策

- Flask 啟動時對前端外殼檔案（`app.js`、`style.css`、模板、Service Worker、第三方程式庫）計算 SHA-256，取前 12 碼作為 `asset_version`。
- HTML 以 `?v=<asset_version>` 載入所有資源；Service Worker 改由 Flask 的 `/sw.js` 提供，帶入同一版本並設定 `Cache-Control: no-cache`。
- 快取名稱包含版本；新版安裝後 `skipWaiting()` 並清除舊快取。
- Leaflet 與 Chart.js 改由本站提供，外部 CDN 失效時查詢仍可運作；Chart.js 在使用者查看趨勢時才載入。

## 影響

- 任何前端檔案內容改變，版本就自動改變，不再有「忘記升版」的問題。
- 從 `/static/sw.js` 移到 `/sw.js` 時，同 scope 的註冊會被新腳本取代，既有使用者自動升級（已在正式環境驗證）。
- 代價：版本在程序啟動時計算，修改前端檔案後必須重啟服務才會生效（部署流程本來就會重啟）。
