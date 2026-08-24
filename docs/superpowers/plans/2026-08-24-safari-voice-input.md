# Safari 語音輸入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 iPhone Safari 與加入主畫面的 PWA 中提供一次性繁體中文語音輸入，辨識結果只填入既有目的地欄位，由使用者確認後再按「分析」。

**Architecture:** 只使用瀏覽器的 `SpeechRecognition`／`webkitSpeechRecognition`，不錄製或上傳音訊到本專案後端，也不改 Flask、Gemini、MySQL 或推薦 API。語音控制集中在既有 `static/app.js` 的單一初始化函式；不支援時隱藏按鈕，保留原本鍵盤輸入。所有靜態資源同步由 `self-use-v2` 升為 `voice-v1`，確保已安裝 PWA 取得新介面。

**Tech Stack:** HTML、CSS、原生 JavaScript Web Speech API、Flask/Jinja 靜態版本參數、pytest 前端/PWA 契約測試

**Spec:** 2026-08-24 Codex 對話中已核准的 bounded design；沒有獨立 spec 文件。

## Global Constraints

- 實作者與任務 reviewer 明確使用 `deepseek-v4-flash`；controller 負責最終 review 與 QA。
- 不新增 npm、Python 套件、後端端點、資料表、外部語音 API 或 API 金鑰。
- 同時偵測 `window.SpeechRecognition` 與 `window.webkitSpeechRecognition`。
- 語言固定 `zh-TW`；`continuous=false`、`interimResults=false`、`maxAlternatives=1`。
- 語音結果只寫入 `#message`，絕對不能自動 submit 或呼叫 `/api/query`。
- 使用者必須主動點擊按鈕才開始收音；再次點擊可停止。
- 不支援時保持語音按鈕隱藏；打字、手動查詢與既有查詢流程不得受影響。
- 拒絕權限、沒有語音、網路失敗及其他錯誤都以既有 `showStatus()` 顯示白話訊息。
- 監聽狀態必須有可見文字、`aria-pressed` 與 CSS 狀態，不只靠顏色。
- 程式碼保持單一職責並加入繁體中文註解；production code 增量控制在 120 行內。
- 靜態資源版本由 `self-use-v2` 統一升為 `voice-v1`，template、service worker 與測試必須完全一致。
- 不修改目前已合併的推薦、步行排序、費率、歷史、analytics 與 Dashboard 行為。

---

### Task 1: Safari 一次性語音輸入與 PWA 快取更新

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `static/sw.js`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_pwa_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `#message` 目的地輸入框、`#status` 狀態區、既有 `showStatus(message, type)`、`DOMContentLoaded` 初始化流程、`static/sw.js` shell cache。
- Produces: `#voice-input` 按鈕、`setupVoiceInput()` 初始化函式、Safari/標準 Web Speech feature detection、`voice-v1` PWA shell。

- [ ] **Step 1: 先新增語音 UI 與行為契約測試**

在 `tests/test_frontend_contract.py` 新增以下測試。契約要鎖定可及性、Safari prefix、一次性辨識、繁體中文、錯誤處理，以及「不得自動送出」：

```python
def test_safari_voice_input_is_optional_accessible_and_never_auto_submits():
    """Safari 語音只填入目的地；不支援時隱藏，且不能自動查詢。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="voice-input"' in template
    assert 'type="button"' in template
    assert 'aria-label="使用語音輸入目的地"' in template
    assert 'hidden' in template.split('id="voice-input"', 1)[1].split(">", 1)[0]
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in script
    assert 'recognition.lang = "zh-TW"' in script
    assert "recognition.continuous = false" in script
    assert "recognition.interimResults = false" in script
    assert "recognition.maxAlternatives = 1" in script
    assert "function setupVoiceInput()" in script
    assert "setupVoiceInput();" in script

    voice = script.split("function setupVoiceInput()", 1)[1].split(
        "function ", 1)[0]
    assert "recognition.onresult" in voice
    assert "input.value = transcript" in voice
    assert 'button.setAttribute("aria-pressed", "true")' in voice
    assert "not-allowed" in voice
    assert "no-speech" in voice
    assert "network" in voice
    assert "submitQuery(" not in voice
    assert ".submit(" not in voice
    assert ".requestSubmit(" not in voice
```

在同一檔案新增狀態文案契約，避免 Safari 錯誤只顯示英文代碼：

```python
def test_voice_input_has_plain_language_listening_and_error_messages():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for message in (
        "正在聆聽，請說出目的地",
        "已填入語音結果，請確認後按分析",
        "請允許 Safari 使用麥克風",
        "沒有聽到語音，請再試一次",
        "語音服務暫時無法連線",
        "語音輸入失敗，請改用鍵盤輸入",
    ):
        assert message in script
```

- [ ] **Step 2: 執行語音契約測試並確認 RED**

Run:

```powershell
python -m pytest tests/test_frontend_contract.py::test_safari_voice_input_is_optional_accessible_and_never_auto_submits tests/test_frontend_contract.py::test_voice_input_has_plain_language_listening_and_error_messages -q
```

Expected: FAIL，至少因 `#voice-input` 與 `setupVoiceInput()` 尚不存在而失敗；若測試直接通過，必須先修正測試使其能捕捉缺少功能。

- [ ] **Step 3: 在既有查詢框加入隱藏的語音按鈕**

將 `templates/index.html` 的聊天輸入列改為下列結構。`type="button"` 是必要條件，避免點麥克風觸發表單 submit：

```html
<div class="input-row">
  <input id="message" required placeholder="例如：今晚六點去臺北市政府，哪裡比較好停？">
  <div class="query-actions">
    <button id="voice-input" class="voice-input" type="button"
            aria-label="使用語音輸入目的地" aria-pressed="false" hidden>
      <span aria-hidden="true">🎙</span><span class="voice-label">語音</span>
    </button>
    <button type="submit">分析</button>
  </div>
</div>
```

不要新增第二個輸入框，也不要改 `#chat-form` submit handler。

- [ ] **Step 4: 實作單一 Safari 語音控制函式**

在 `static/app.js` 表單事件之前加入以下同等行為的實作；允許依現有格式調整空白，但不得增加自動 submit：

```javascript
// Safari 可能只提供 webkit 前綴；兩者都沒有時維持純鍵盤輸入。
const SpeechRecognitionApi =
  window.SpeechRecognition || window.webkitSpeechRecognition;

function setupVoiceInput() {
  const button = document.querySelector("#voice-input");
  const input = document.querySelector("#message");
  if (!button || !input || !SpeechRecognitionApi) return;

  const recognition = new SpeechRecognitionApi();
  recognition.lang = "zh-TW";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  let listening = false;

  const resetButton = () => {
    listening = false;
    button.classList.remove("listening");
    button.setAttribute("aria-pressed", "false");
    button.querySelector(".voice-label").textContent = "語音";
  };

  button.hidden = false;
  button.addEventListener("click", () => {
    if (listening) {
      recognition.stop();
      return;
    }
    try {
      recognition.start();
    } catch {
      showStatus("語音輸入失敗，請改用鍵盤輸入", "error");
      resetButton();
    }
  });

  recognition.onstart = () => {
    listening = true;
    button.classList.add("listening");
    button.setAttribute("aria-pressed", "true");
    button.querySelector(".voice-label").textContent = "停止";
    showStatus("正在聆聽，請說出目的地");
  };

  recognition.onresult = event => {
    const transcript = String(event.results?.[0]?.[0]?.transcript || "").trim();
    if (!transcript) return;
    input.value = transcript;
    input.focus();
    showStatus("已填入語音結果，請確認後按分析", "success");
  };

  recognition.onerror = event => {
    const messages = {
      "not-allowed": "請允許 Safari 使用麥克風",
      "service-not-allowed": "請允許 Safari 使用麥克風",
      "no-speech": "沒有聽到語音，請再試一次",
      "network": "語音服務暫時無法連線",
    };
    showStatus(
      messages[event.error] || "語音輸入失敗，請改用鍵盤輸入",
      "error",
    );
  };

  recognition.onend = resetButton;
}
```

在現有 `DOMContentLoaded` callback 中呼叫一次：

```javascript
setupVoiceInput();
```

不要把 recognition、音訊或 transcript 傳給新的後端端點；transcript 只有在使用者按「分析」後，才沿用原本聊天文字流程。

- [ ] **Step 5: 加入清楚且不破壞行動版的按鈕樣式**

在 `static/style.css` 沿用現有按鈕語言，加入：

```css
.query-actions { display:flex; gap:8px; }
.query-actions button { white-space:nowrap; }
.voice-input { background:#17212c; color:#d7e2ec; border:1px solid #3a4a5b; }
.voice-input.listening { background:#7d2430; color:#fff; border-color:#ff9298; }
```

在既有手機 media query 中加入：

```css
.query-actions { display:grid; grid-template-columns:1fr 1fr; }
```

保留目前 `.input-row` 的手機單欄配置，使輸入框一列、兩個操作按鈕下一列；不要把麥克風做成只有顏色、沒有文字的按鈕。

- [ ] **Step 6: 執行語音測試並確認 GREEN**

Run:

```powershell
python -m pytest tests/test_frontend_contract.py::test_safari_voice_input_is_optional_accessible_and_never_auto_submits tests/test_frontend_contract.py::test_voice_input_has_plain_language_listening_and_error_messages -q
node --check static/app.js
```

Expected: 2 passed；`node --check` exit 0。

- [ ] **Step 7: 先把 PWA 版本契約改為 voice-v1 並確認 RED**

在 `tests/test_frontend_contract.py` 與 `tests/test_pwa_contract.py` 將目前 `self-use-v2` 契約統一改成 `voice-v1`，測試名稱同步改為 `test_pwa_asset_versions_bumped_for_voice_input`。保留「template 與 service worker 必須一致」的全部斷言。

Run:

```powershell
python -m pytest tests/test_frontend_contract.py tests/test_pwa_contract.py -q
```

Expected: FAIL，且失敗原因只能是 production template／service worker 仍為 `self-use-v2`。

- [ ] **Step 8: 同步更新 template 與 service worker cache**

只做以下字串更新：

```text
templates/index.html:
  style.css v='voice-v1'
  app.js v='voice-v1'

static/sw.js:
  CACHE_NAME = "parking-radar-shell-voice-v1"
  /static/style.css?v=voice-v1
  /static/app.js?v=voice-v1
```

保留既有 `activate` 清除其他 cache 的邏輯，讓 `self-use-v2` 自動淘汰。

- [ ] **Step 9: 更新 README 的使用與限制說明**

在功能列表加入：

```markdown
- iPhone Safari／主畫面 PWA 可用一次性繁體中文語音輸入；辨識結果只填入目的地欄位，確認後才送出。
```

在 PWA 限制補充：

```markdown
語音輸入使用 Safari 的 Web Speech API；按鈕未出現時仍可使用 iPhone 鍵盤聽寫。首次使用需允許麥克風，切到背景時 Safari 會停止辨識。本專案不保存音訊，但 Safari 的辨識服務是否使用網路由瀏覽器與系統決定。
```

- [ ] **Step 10: 執行完整驗證與範圍檢查**

Run:

```powershell
python -m pytest tests -q
python -m compileall -q app.py analysis.py
node --check static/app.js
node --check static/sw.js
git diff --check
git diff --stat master...HEAD
```

Expected:

- pytest 全部通過，基準為至少 381 項加上本計畫新增測試。
- Python compileall 與兩個 Node checks exit 0。
- `git diff --check` 無錯誤。
- production code 增量不超過 120 行。
- 無 Flask、MySQL、Gemini、分析或推薦檔案變更。

- [ ] **Step 11: 記錄實機 Safari 驗收清單**

在 implementer report 明確列出以下人工驗收；沒有實際 iPhone 時必須標記「待使用者實機驗收」，不得假稱通過：

```text
1. iPhone Safari 開啟 HTTPS 網站，語音按鈕可見。
2. 第一次點擊出現麥克風權限；允許後顯示「正在聆聽」。
3. 說「我要去台北車站」後，只填入文字，不自動分析。
4. 按分析後沿用原本查詢流程並正常顯示推薦。
5. 再次點擊可停止；拒絕權限與沒有聲音都有中文提示。
6. 加入主畫面的 PWA 重複 1–5。
7. 不支援或 API 暫時不可用時，鍵盤輸入與 iPhone 鍵盤聽寫仍可使用。
```

- [ ] **Step 12: 自我 review 並提交**

確認沒有 `submitQuery()`、`.submit()` 或 `.requestSubmit()` 出現在 `setupVoiceInput()`，沒有新增任何錄音上傳或依賴，再提交：

```powershell
git add templates/index.html static/app.js static/style.css static/sw.js README.md tests/test_frontend_contract.py tests/test_pwa_contract.py
git commit -m "feat: add Safari voice destination input"
```

提交後由 SDD controller 依序執行：Task spec review、Task quality review、必要 fix loop、final whole-branch review，最後由 controller 重新跑完整 QA。

