# Explainable Parking Decision Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw parking scores with understandable decision cards that show a conclusion, deterministic reasons, full address, remaining/total spaces, Google Maps, and history actions.

**Architecture:** Keep Gemini limited to intent parsing. Extend the pure analysis layer to assign one mutually exclusive decision status and up to three rule-based reasons, pass those fields through the existing Flask API, and let the current vanilla JavaScript render the approved A card design. Google Maps uses a free web URL built from existing coordinates or address data, so no new service or dependency is introduced.

**Tech Stack:** Python 3.11, Flask, PyMySQL, pytest, vanilla JavaScript, HTML, CSS, existing Leaflet and Chart.js.

## Global Constraints

- Do not use Gemini to calculate scores, assign parking status, or write recommendation reasons.
- Do not add a database table, Python package, JavaScript package, Google Maps API key, or paid API.
- Keep the current recommendation weights and 1,500-metre search radius unchanged.
- A decision card must show conclusion, parking name, full address, remaining/total spaces, distance or district mode, up to three reasons, Google Maps, history action, and secondary score labels.
- Decision groups must be mutually exclusive: `avoid` takes precedence over `warning`, which takes precedence over `recommended`.
- `avoid` means `available_spaces <= 3` or `hell_score >= 95`; `warning` means a non-avoid lot with `85 <= hell_score < 95`; all other valid candidates are `recommended`.
- Google Maps links open a new tab with `rel="noopener noreferrer"` and use coordinates before address search text.
- The API and page show separate `official_updated_at` and `collected_at` values converted from UTC to `Asia/Taipei`; legacy `updated_at` remains equal to `collected_at`.
- Run `collector.py --once` through operating-system scheduling every 15 minutes; do not add a scheduler to Flask.
- Every new Python function and non-obvious JavaScript or CSS rule receives a concise Traditional Chinese comment.
- Keep desktop cards at three columns and mobile cards at one column.
- Preserve the existing history chart, map markers, manual query, chat query, and error handling.

---

## File Structure

- Modify `analysis.py`: own deterministic decision status, labels, reasons, and mutually exclusive grouping.
- Modify `app.py`: include decision fields in candidate JSON and return separate official and collection times.
- Modify `static/app.js`: own full-address formatting, Google Maps URL construction, card markup, and card button events.
- Modify `static/style.css`: own approved A-card hierarchy, state colours, capacity bar, reason panel, and actions.
- Modify `templates/index.html`: add a concise explanation above the cards if needed; do not add a page.
- Modify `README.md`: document the analysis boundary, dual timestamps, Windows scheduling, and 15-minute GCP cron.
- Modify `tests/test_analysis.py`: verify status boundaries, reason text, score labels, and non-overlapping groups.
- Modify `tests/test_app_routes.py`: verify the API preserves decision fields and existing parking fields.
- Create `tests/test_frontend_contract.py`: protect the Google Maps, address, capacity, reason, and history hooks without adding a JavaScript test framework.

---

### Task 1: Deterministic Decision Explanations

**Files:**
- Modify: `analysis.py:105-114`
- Modify: `tests/test_analysis.py`

**Interfaces:**
- Consumes: ranked candidate dictionaries containing `lot_id`, `total_spaces`, `available_spaces`, `hell_score`, `recommendation_score`, `distance_m`, `historical_hell_score`, and `history_sample_count`.
- Produces: `explain_candidate(row: dict, min_history_samples: int = 3) -> dict` with `decision_status`, `decision_label`, `pressure_label`, `recommendation_label`, and `reasons` added to a copy of `row`.
- Produces: `split_recommendation_groups(ranked: list[dict]) -> dict[str, list[dict]]` with mutually exclusive `recommendations`, `warning`, and `avoid` lists plus the existing independent `nearest` list.

- [ ] **Step 1: Write failing explanation tests**

Add focused tests to `tests/test_analysis.py`:

```python
def decision_row(**overrides):
    """建立決策說明測試所需的完整候選停車場。"""
    row = {
        "lot_id": "SAFE", "total_spaces": 5, "available_spaces": 5,
        "hell_score": 0.0, "recommendation_score": 91.66,
        "distance_m": 312.5, "historical_hell_score": None,
        "history_sample_count": 1,
    }
    row.update(overrides)
    return row


def test_explain_candidate_translates_scores_into_reasons():
    result = analysis.explain_candidate(decision_row())

    assert result["decision_status"] == "recommended"
    assert result["decision_label"] == "建議前往"
    assert result["pressure_label"] == "低"
    assert result["recommendation_label"] == "高"
    assert result["reasons"] == [
        "目前 5 / 5 格可停，空位充足",
        "距目的地近，約 313 公尺",
        "歷史樣本不足，未納入判斷",
    ]


@pytest.mark.parametrize(("row", "status", "label", "action"), [
    (decision_row(available_spaces=1, hell_score=80.0),
     "avoid", "不建議前往", "建議改看推薦前往清單"),
    (decision_row(available_spaces=5, total_spaces=50, hell_score=90.0),
     "warning", "有滿場風險", "建議保留下一個選擇"),
])
def test_explain_candidate_adds_risk_action(row, status, label, action):
    result = analysis.explain_candidate(row)

    assert result["decision_status"] == status
    assert result["decision_label"] == label
    assert action in result["reasons"]
    assert len(result["reasons"]) <= 3
```

- [ ] **Step 2: Run the explanation tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_analysis.py -k "explain_candidate" -q
```

Expected: FAIL because `analysis.explain_candidate` does not exist.

- [ ] **Step 3: Implement score labels and fixed reasons**

Add the following pure helpers near `split_recommendation_groups` in `analysis.py`:

```python
def _pressure_label(score):
    """把停車壓力分數翻譯成低、中、高、極高。"""
    if score >= 95:
        return "極高"
    if score >= 85:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def _recommendation_label(score):
    """把綜合推薦分數翻譯成高、中、低。"""
    if score >= 80:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def explain_candidate(row, min_history_samples=3):
    """用固定規則產生決策狀態與最多三條白話原因。"""
    item = dict(row)
    available = int(item["available_spaces"])
    total = int(item["total_spaces"])
    pressure = float(item["hell_score"])
    recommendation = float(item["recommendation_score"])

    if available <= 3 or pressure >= 95:
        status, label = "avoid", "不建議前往"
    elif pressure >= 85:
        status, label = "warning", "有滿場風險"
    else:
        status, label = "recommended", "建議前往"

    if available == 0:
        availability_reason = "目前已滿場"
    elif available <= 3:
        availability_reason = f"目前只剩 {available} 格，抵達前可能滿場"
    elif pressure >= 85:
        availability_reason = f"目前 {available} / {total} 格可停，滿場風險偏高"
    else:
        availability_reason = f"目前 {available} / {total} 格可停，空位充足"

    distance = item.get("distance_m")
    if distance is None:
        distance_reason = "目前以行政區整體狀況比較"
    elif distance <= 500:
        distance_reason = f"距目的地近，約 {round(distance)} 公尺"
    elif distance <= 1000:
        distance_reason = f"距目的地約 {round(distance)} 公尺"
    else:
        distance_reason = f"距目的地較遠，約 {distance / 1000:.1f} 公里"

    if status == "avoid":
        final_reason = "建議改看推薦前往清單"
    elif status == "warning":
        final_reason = "建議保留下一個選擇"
    elif (item.get("history_sample_count") or 0) < min_history_samples:
        final_reason = "歷史樣本不足，未納入判斷"
    else:
        historical = item.get("historical_hell_score")
        final_reason = (
            f"相同時段歷史停車壓力約 {round(historical)} 分"
            if historical is not None else "歷史樣本不足，未納入判斷"
        )

    item.update(
        decision_status=status,
        decision_label=label,
        pressure_label=_pressure_label(pressure),
        recommendation_label=_recommendation_label(recommendation),
        reasons=[availability_reason, distance_reason, final_reason],
    )
    return item
```

- [ ] **Step 4: Run explanation tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_analysis.py -k "explain_candidate" -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Write a failing group exclusivity test**

Add to `tests/test_analysis.py`:

```python
def test_decision_groups_are_mutually_exclusive():
    ranked = [
        decision_row(lot_id="SAFE"),
        decision_row(lot_id="WARN", total_spaces=50, available_spaces=5,
                     hell_score=90.0, recommendation_score=70.0),
        decision_row(lot_id="AVOID", total_spaces=8, available_spaces=1,
                     hell_score=87.5, recommendation_score=40.0),
    ]

    groups = analysis.split_recommendation_groups(ranked)

    assert [row["lot_id"] for row in groups["recommendations"]] == ["SAFE"]
    assert [row["lot_id"] for row in groups["warning"]] == ["WARN"]
    assert [row["lot_id"] for row in groups["avoid"]] == ["AVOID"]
    ids = [row["lot_id"] for name in ("recommendations", "warning", "avoid")
           for row in groups[name]]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 6: Run the exclusivity test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_analysis.py::test_decision_groups_are_mutually_exclusive -q
```

Expected: FAIL because the existing top-three recommendation list overlaps with warning and avoid.

- [ ] **Step 7: Make decision groups mutually exclusive**

Replace `split_recommendation_groups` with:

```python
def split_recommendation_groups(ranked):
    """加入決策說明並產生互斥的推薦、警示與避雷群組。"""
    explained = [explain_candidate(row) for row in ranked]
    with_distance = [row for row in explained if row.get("distance_m") is not None]
    nearest = sorted(with_distance, key=lambda item: item["distance_m"])[:3]
    recommendations = [
        row for row in explained if row["decision_status"] == "recommended"][:3]
    warning = [row for row in explained if row["decision_status"] == "warning"][:3]
    avoid = [row for row in explained if row["decision_status"] == "avoid"][:3]
    return {
        "recommendations": recommendations,
        "nearest": nearest,
        "warning": warning,
        "avoid": avoid,
    }
```

- [ ] **Step 8: Run all analysis tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_analysis.py -q
```

Expected: all analysis tests PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add analysis.py tests/test_analysis.py
git commit -m "feat: explain parking recommendations with fixed rules"
```

---

### Task 2: Publish Decision Fields and Data Freshness Through the Flask API

**Files:**
- Modify: `app.py:69-83`
- Modify: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: explained candidate dictionaries returned by `split_recommendation_groups`.
- Produces: public candidate JSON containing `decision_status: str`, `decision_label: str`, `pressure_label: str`, `recommendation_label: str`, `reasons: list[str]`, plus existing `address`, `district`, `total_spaces`, `available_spaces`, `latitude`, `longitude`, and score fields.
- Produces: top-level `official_updated_at: str | None`, `collected_at: str | None`, and backward-compatible `updated_at == collected_at`.

- [ ] **Step 1: Write a failing public candidate test**

Add to `tests/test_app_routes.py`:

```python
def test_public_candidate_keeps_decision_card_fields():
    row = lot_row()
    row.update({
        "decision_status": "recommended",
        "decision_label": "建議前往",
        "pressure_label": "低",
        "recommendation_label": "高",
        "reasons": ["目前 20 / 100 格可停", "距目的地近，約 300 公尺"],
    })

    result = app_module.public_candidate(row)

    assert result["address"] == row["address"]
    assert result["total_spaces"] == row["total_spaces"]
    assert result["decision_status"] == "recommended"
    assert result["decision_label"] == "建議前往"
    assert result["pressure_label"] == "低"
    assert result["recommendation_label"] == "高"
    assert result["reasons"] == row["reasons"]
```

- [ ] **Step 2: Run the route test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_routes.py::test_public_candidate_keeps_decision_card_fields -q
```

Expected: FAIL because the allowlist currently drops the five new decision fields.

- [ ] **Step 3: Extend the public JSON allowlist**

In `public_candidate`, add the five fields to `keys` while leaving numeric conversion unchanged:

```python
    keys = (
        "lot_id", "lot_name", "district", "address", "operator_type",
        "total_spaces", "available_spaces", "fee_info", "service_time",
        "hell_label", "history_sample_count", "decision_status",
        "decision_label", "pressure_label", "recommendation_label", "reasons",
    )
```

- [ ] **Step 4: Run all route and error tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_routes.py tests\test_app_errors.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Write a failing dual-timestamp route test**

Extend the existing timestamp test in `tests/test_app_routes.py` so its fake row contains both database times:

```python
def test_query_returns_official_and_collection_times_in_taipei(monkeypatch):
    """官方時間與抓取時間都要從 MySQL UTC 正確轉成台北時間。"""
    connection = CloseTrackingConnection()
    row = lot_row(datetime(2026, 8, 3, 10))
    row["snapshot_updated_at"] = datetime(2026, 8, 3, 9, 55)
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [row])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-03T18:00:00+08:00",
    })
    body = response.get_json()

    assert response.status_code == 200
    assert body["official_updated_at"] == "2026-08-03T17:55:00+08:00"
    assert body["collected_at"] == "2026-08-03T18:00:00+08:00"
    assert body["updated_at"] == body["collected_at"]
```

- [ ] **Step 6: Run the timestamp test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_routes.py::test_query_returns_official_and_collection_times_in_taipei -q
```

Expected: FAIL because the response has only `updated_at`.

- [ ] **Step 7: Return both timestamps without duplicating timezone logic**

Add this helper near the other route helpers in `app.py`:

```python
def taipei_iso(value):
    """把 MySQL 的 naive UTC datetime 轉成台北 ISO 字串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Taipei")).isoformat()
```

Replace the single `updated_at` calculation in `query_parking` with:

```python
            collected_at = taipei_iso(max(
                (row["captured_at"] for row in rows), default=None))
            official_updated_at = taipei_iso(max(
                (row.get("snapshot_updated_at") for row in rows
                 if row.get("snapshot_updated_at") is not None),
                default=None,
            ))
```

Add these keys to `jsonify`:

```python
                official_updated_at=official_updated_at,
                collected_at=collected_at,
                updated_at=collected_at,
```

- [ ] **Step 8: Run all route and error tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_routes.py tests\test_app_errors.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 9: Exercise the live API contract**

With the local Flask server and populated MySQL running, execute:

```powershell
@'
import requests
data = requests.post(
    "http://127.0.0.1:5000/api/query",
    json={"mode": "chat", "message": "我要去資策會"},
    timeout=60,
).json()
card = data["recommendations"][0]
required = {
    "address", "total_spaces", "available_spaces", "latitude", "longitude",
    "decision_status", "decision_label", "pressure_label",
    "recommendation_label", "reasons",
}
print("DECISION_CARD_CONTRACT_OK", required <= set(card))
print("REASON_COUNT", len(card["reasons"]))
print("OFFICIAL_UPDATED_AT", data["official_updated_at"])
print("COLLECTED_AT", data["collected_at"])
'@ | .\.venv\Scripts\python.exe -
```

Expected: `DECISION_CARD_CONTRACT_OK True`, `REASON_COUNT 3`, and two non-empty Taiwan-offset timestamp strings. Do not print API keys or database credentials.

- [ ] **Step 10: Commit Task 2**

```powershell
git add app.py tests/test_app_routes.py
git commit -m "feat: expose parking decision explanations"
```

---

### Task 3: Render Decision Cards and Google Maps Links

**Files:**
- Modify: `static/app.js:20-47`
- Modify: `static/style.css:1-28`
- Modify: `templates/index.html:35`
- Modify: `README.md`
- Create: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `data.recommendations`, `data.warning`, and `data.avoid` candidates containing all Task 2 public fields.
- Produces: `formatFullAddress(lot: object) -> string`, `googleMapsUrl(lot: object) -> string | null`, and accessible card markup with `.parking-card`, `.capacity-bar`, `.reason-list`, `.maps-link`, and `[data-lot]` history buttons.

- [ ] **Step 1: Write a failing frontend contract test**

Create `tests/test_frontend_contract.py`:

```python
"""前端圖卡契約測試：避免地址、容量、理由與兩種操作被意外移除。"""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_decision_cards_keep_required_data_and_actions():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function formatFullAddress(lot)" in script
    assert "function googleMapsUrl(lot)" in script
    assert "https://www.google.com/maps/search/?api=1&query=" in script
    assert "lot.total_spaces" in script
    assert "lot.reasons" in script
    assert "data.official_updated_at" in script
    assert "data.collected_at" in script
    assert 'target="_blank"' in script
    assert 'rel="noopener noreferrer"' in script
    assert 'data-lot="${lot.lot_id}"' in script
```

- [ ] **Step 2: Run the frontend contract test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_contract.py -q
```

Expected: FAIL because the address and Google Maps helpers do not exist.

- [ ] **Step 3: Add address and Google Maps helpers**

Add above `parkingCard` in `static/app.js`:

```javascript
// 轉義官方文字，避免 innerHTML 把名稱、地址或原因當成標記執行。
function escapeHtml(value) {
  const entities = {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"};
  return String(value ?? "").replace(/[&<>"']/g, character => entities[character]);
}

// 組合臺北市、行政區與官方地址，避免重複顯示城市或行政區。
function formatFullAddress(lot) {
  const address = (lot.address || "").replaceAll("台北市", "臺北市").trim();
  if (!address) return "地址資料未提供";
  if (address.startsWith("臺北市")) return address;
  if (lot.district && address.startsWith(lot.district)) return `臺北市${address}`;
  return `臺北市${lot.district || ""}${address}`;
}

// 優先用精確座標開啟 Google 地圖，缺少座標時改以名稱與地址搜尋。
function googleMapsUrl(lot) {
  const base = "https://www.google.com/maps/search/?api=1&query=";
  if (lot.latitude != null && lot.longitude != null) {
    return `${base}${encodeURIComponent(`${lot.latitude},${lot.longitude}`)}`;
  }
  const address = formatFullAddress(lot);
  if (address === "地址資料未提供" && !lot.lot_name) return null;
  return `${base}${encodeURIComponent(`${lot.lot_name || ""} ${address}`.trim())}`;
}
```

- [ ] **Step 4: Replace raw score markup with the approved A card**

Replace `parkingCard` with markup following this exact data hierarchy:

```javascript
function parkingCard(lot) {
  const address = formatFullAddress(lot);
  const mapsUrl = googleMapsUrl(lot);
  const safeMapsUrl = mapsUrl ? escapeHtml(mapsUrl) : null;
  const freePercent = Math.max(0, Math.min(
    100, lot.available_spaces / lot.total_spaces * 100));
  const distance = formatDistance(lot.distance_m);
  const reasons = (lot.reasons || [])
    .map(reason => `<li>${escapeHtml(reason)}</li>`).join("");
  const mapsLink = safeMapsUrl
    ? `<a class="maps-link" href="${safeMapsUrl}" target="_blank" rel="noopener noreferrer">Google 地圖 ↗</a>`
    : `<span class="maps-link disabled" aria-disabled="true">無地圖資料</span>`;

  return `<article class="parking-card ${escapeHtml(lot.decision_status)}">
    <div class="card-top">
      <span class="decision-badge">${escapeHtml(lot.decision_label)}</span>
      <span class="distance-label">${escapeHtml(distance)}</span>
    </div>
    <h3>${escapeHtml(lot.lot_name)}</h3>
    ${safeMapsUrl
      ? `<a class="parking-address" href="${safeMapsUrl}" target="_blank" rel="noopener noreferrer">📍 ${escapeHtml(address)} ↗</a>`
      : `<span class="parking-address">📍 ${escapeHtml(address)}</span>`}
    <div class="capacity"><strong>${lot.available_spaces} / ${lot.total_spaces}</strong><span>格目前可停</span></div>
    <div class="capacity-bar" aria-label="空位比例"><i style="width:${freePercent}%"></i></div>
    <div class="reason-panel"><strong>判斷原因</strong><ul class="reason-list">${reasons}</ul></div>
    <div class="card-actions">${mapsLink}<button type="button" data-lot="${escapeHtml(lot.lot_id)}">查看歷史</button></div>
    <small class="score-details">停車壓力${escapeHtml(lot.pressure_label)}｜綜合推薦${escapeHtml(lot.recommendation_label)}</small>
  </article>`;
}
```

Keep `data-lot` so the existing history event binding continues to call `loadHistory`. URL values must come only from `googleMapsUrl`.

- [ ] **Step 5: Render mutually exclusive groups with empty-state copy**

Update `renderCards` so it calls `parkingCard(x)` without CSS kind arguments and preserves backend order:

```javascript
function renderCards(data) {
  const cards = [
    ...data.recommendations.map(parkingCard),
    ...data.warning.map(parkingCard),
    ...data.avoid.map(parkingCard),
  ];
  const noRecommendation = data.recommendations.length
    ? "" : "<p class=\"group-empty\">目前沒有低風險停車場，請查看警示與避雷建議。</p>";
  const noCandidates = cards.length ? "" : "<p>目前沒有可分析的停車場。</p>";
  document.querySelector("#recommendations").innerHTML =
    noRecommendation + (cards.join("") || noCandidates);
  document.querySelector("#nearest").innerHTML = data.nearest.map(x =>
    `<li><button type="button" data-lot="${x.lot_id}">${x.lot_name}<br>${formatDistance(x.distance_m)}</button></li>`
  ).join("");
  document.querySelectorAll("[data-lot]").forEach(button =>
    button.addEventListener("click", () =>
      loadHistory(button.dataset.lot).catch(error => showStatus(error.message, "error"))));
}
```

- [ ] **Step 6: Implement the approved visual hierarchy**

In `static/style.css`, replace the three border-only card state rules with focused styles for:

```css
/* 決策圖卡以狀態色、容量、原因與操作建立清楚的閱讀順序。 */
.parking-card { display:flex; flex-direction:column; min-height:430px; border-top:4px solid var(--accent); }
.parking-card.recommended { --accent:var(--green); }
.parking-card.warning { --accent:var(--yellow); }
.parking-card.avoid { --accent:var(--red); }
.card-top { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.decision-badge { padding:5px 10px; border-radius:999px; color:var(--accent); background:#0f151c; font-weight:800; }
.parking-address { display:block; min-height:42px; color:#b8c3d0; text-decoration:underline; text-underline-offset:3px; }
.capacity { display:flex; align-items:end; gap:8px; margin-top:18px; }
.capacity strong { font-size:2rem; line-height:1; }
.capacity-bar { height:8px; margin:11px 0 5px; overflow:hidden; border-radius:999px; background:#29323e; }
.capacity-bar i { display:block; height:100%; background:var(--accent); }
.reason-panel { margin:16px 0; padding:12px; border-radius:12px; background:#0f151c; }
.reason-list { margin:7px 0 0; padding-left:18px; }
.reason-list li::marker { color:var(--accent); }
.card-actions { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:auto; }
.maps-link, .card-actions button { display:grid; place-items:center; min-height:42px; border-radius:9px; font-weight:800; }
.maps-link { background:var(--orange); color:#211108; text-decoration:none; }
.maps-link.disabled { opacity:.45; cursor:not-allowed; }
.score-details { margin-top:10px; color:var(--muted); text-align:center; }
```

Retain the existing responsive rule that changes `.card-grid` to one column below 760 pixels. Adjust spacing only where required by the live cards; do not redesign the hero, forms, map, or chart.

- [ ] **Step 7: Clarify the section heading and README**

In `templates/index.html`, keep the heading `推薦與避雷` and add this sentence immediately below it:

```html
<p class="section-note">依即時空位、距離與歷史資料，用固定規則說明判斷原因。</p>
```

Replace the single `#updated-at` node in the hero with:

```html
<p class="data-times" aria-live="polite">
  <span id="official-updated-at">官方資料時間：尚未查詢</span>
  <span id="collected-at">系統最後抓取：尚未查詢</span>
</p>
```

Update `renderSummary` in `static/app.js`:

```javascript
  const officialTime = data.official_updated_at
    ? new Date(data.official_updated_at).toLocaleString("zh-TW") : "無資料";
  const collectedTime = data.collected_at
    ? new Date(data.collected_at).toLocaleString("zh-TW") : "無資料";
  document.querySelector("#official-updated-at").textContent =
    `官方資料時間：${officialTime}`;
  document.querySelector("#collected-at").textContent =
    `系統最後抓取：${collectedTime}`;
```

Add `.data-times` styling that wraps on small screens and keeps both timestamps visually secondary.

In `README.md`, add under `計算與資料清洗`:

```markdown
- 圖卡的推薦、警示、避雷與白話原因全部由 Python 固定規則產生；Gemini 只解析對話條件。
- 停車場地址與 Google 地圖連結使用既有座標或地址，不使用付費 Google Maps API。
- 頁面分開顯示官方動態資料時間與本系統抓取時間，避免誤判資料新鮮度。
```

Under `Windows 本機啟動`, document optional 15-minute Windows Task Scheduler registration:

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

Change the existing GCP cron from `*/30` to:

```cron
*/15 * * * * cd /opt/parking-hell && /opt/parking-hell/.venv/bin/python collector.py --once >> /opt/parking-hell/collector.log 2>&1
```

- [ ] **Step 8: Run frontend and full regression checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_frontend_contract.py -q
node --check static\app.js
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app.py ai_service.py analysis.py collector.py config.py database.py geocoder.py
```

Expected: frontend contract passes, Node exits 0, full pytest passes, and compileall exits 0.

- [ ] **Step 9: Perform live browser acceptance**

Run the local Flask app, submit `我要去資策會`, and inspect desktop and narrow mobile widths. Verify:

- Each rendered card has exactly one decision badge.
- `available_spaces / total_spaces` matches the API response.
- Address text is visible and opens a new Google Maps tab at the parking location.
- The Google Maps button opens the same location.
- The history button updates the existing Chart.js chart without navigating away.
- Recommended, warning, and avoid cards use green, yellow, and red state colours.
- Exact raw scores are not the main heading and the three reasons explain the status.
- No parking lot appears in more than one of the three decision groups.
- The hero shows separate, correctly labelled official and collection times.

- [ ] **Step 10: Commit Task 3**

```powershell
git add static/app.js static/style.css templates/index.html README.md tests/test_frontend_contract.py
git commit -m "feat: render explainable parking decision cards"
```

---

## Final Verification

- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` and record the passing count.
- [ ] Run `.\.venv\Scripts\python.exe -m compileall -q app.py ai_service.py analysis.py collector.py config.py database.py geocoder.py` and confirm exit code 0.
- [ ] Run `node --check static\app.js` and confirm exit code 0.
- [ ] Submit both `我要去信義區` and `我要去資策會` against the live API and confirm HTTP 200.
- [ ] Confirm both live responses contain non-empty `official_updated_at` and `collected_at` values with `+08:00` offsets.
- [ ] Confirm README contains the GCP `*/15` cron and Windows 15-minute task instructions.
- [ ] Inspect `git status --short`; preserve the existing user-owned `.env.example` MySQL changes unless the user explicitly asks to commit them.
