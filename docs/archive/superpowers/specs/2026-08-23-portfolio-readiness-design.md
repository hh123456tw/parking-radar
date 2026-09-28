# 停車地獄雷達作品集整理設計

## 目標

讓公開 GitHub、正式網站與本機主分支保持一致，並用最少變更完成三項改善：可自動驗證的 CI、面試官能快速讀懂的 README，以及從停車場圖卡直接開啟 Google Maps 導航。

## 範圍

- 將本機 `master` 的效能修正推送至 `origin/master`。
- 新增 GitHub Actions，在 push 與 pull request 執行 pytest、Python 編譯及 JavaScript 語法檢查。
- 重整 README 第一屏，加入 Live Demo、產品截圖、技術棧、Mermaid 架構圖、工程決策與測試資訊；保留現有安裝及 GCP runbook，但移到後段。
- 將既有 Google Maps 搜尋網址改為 Directions URL；起點交由 Google Maps 使用裝置目前位置，本站不要求 GPS 權限。
- 更新 PWA shell cache 版本，避免手機繼續使用舊 JavaScript。
- 依既有備份／原子切換方式部署，完成桌面、手機與正式 API 驗收。

## 不在範圍

- Docker、Docker Compose、Kubernetes、Prometheus、Grafana。
- 會員、收藏、目的地歷史、站內 GPS 搜尋。
- ML 空位預測、Redis、Celery、微服務。
- 推薦公式、資料庫 schema、Gemini contract 或 API response 格式變更。

## 使用者行為

推薦卡與其他場站都保留一個地圖操作。點擊後使用：

```text
https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=<座標或停車場地址>
```

若有座標，以 `latitude,longitude` 當目的地；否則使用停車場名稱加完整地址。Google Maps 負責取得起點、導航能力與權限，本站不存取使用者位置。

## README 架構

第一屏依序呈現：一句話價值、Live Demo、CI badge、產品截圖與技術棧。後續依序為核心功能、系統架構、工程決策、測試與 QA、資料來源、安裝及部署。不得宣稱尚未實作的導航、預測或監控能力。

## CI

GitHub Actions 使用 Ubuntu、Python 3.13、Node.js 22。測試不得使用正式 API 金鑰或真實 MySQL；現有離線測試必須全部通過。工作流程只執行讀取與測試，不負責自動部署。

## 驗收

- `python -m pytest -q` 全數通過。
- Python 編譯、`node --check` 與 `git diff --check` 通過。
- GitHub Actions 在 feature branch 顯示綠色。
- README 的 Live Demo、圖片與 Mermaid 可在 GitHub 正常顯示。
- 正式站查詢「台北車站」後，首選卡與精簡場站的操作連到 `maps/dir`，不是 `maps/search`。
- 正式站 `/health` 正常，查詢與 PWA 不退化，部署前版本可還原。
