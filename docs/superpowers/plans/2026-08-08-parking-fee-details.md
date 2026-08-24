# Parking Fee Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compact, expandable official fee and service-time section to each primary parking card.

**Architecture:** Reuse `fee_info` and `service_time` already present in each public candidate. Render them only inside `primaryCard` with native `<details>` semantics, and constrain long content entirely through focused CSS without changing backend data or ranking behavior.

**Tech Stack:** Vanilla JavaScript, HTML `<details>`/`<summary>`, CSS, pytest contract tests

## Global Constraints

- Only primary recommendation cards display fee details; compact nearby-lot rows remain unchanged.
- Preserve and escape the complete official text; do not parse, summarize, or estimate prices.
- Display `官方未提供` for null, empty, or whitespace-only values.
- Keep expanded content at a maximum height of 160px with internal vertical scrolling.
- Do not modify the database, collector, API contract, recommendation score, or ranking.
- Per user direction, implementation may precede regression-test additions; existing and new tests must still pass before completion.

---

### Task 1: Render and style fee details

**Files:**
- Modify: `static/app.js:188-217`
- Modify: `static/style.css:66-96`

**Interfaces:**
- Consumes: `lot.fee_info`, `lot.service_time`, and existing `escapeHtml(value)`.
- Produces: `.parking-details`, `.parking-details-content`, and `.parking-detail-item` markup inside each `primaryCard`.

- [x] **Step 1: Normalize display values in `primaryCard`**

Create trimmed display values and use `官方未提供` when a value is absent:

```js
const feeInfo = String(lot.fee_info || "").trim() || "官方未提供";
const serviceTime = String(lot.service_time || "").trim() || "官方未提供";
```

- [x] **Step 2: Add the native expandable section**

Insert the following structure between `.decision-summary` and `.card-actions`, escaping both official values:

```html
<details class="parking-details">
  <summary>費率與營業時間</summary>
  <div class="parking-details-content">
    <div class="parking-detail-item"><strong>官方費率</strong><p>...</p></div>
    <div class="parking-detail-item"><strong>營業時間</strong><p>...</p></div>
  </div>
</details>
```

- [x] **Step 3: Add constrained long-text styling**

Style the details element as a low-emphasis secondary panel. Set `.parking-details-content` to `max-height:160px` and `overflow-y:auto`; set detail paragraphs to `white-space:pre-wrap` and `overflow-wrap:anywhere`.

### Task 2: Add regression contracts and verify

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: rendered source contract in `static/app.js` and long-text rules in `static/style.css`.
- Produces: regression coverage for placement, escaping, missing-value copy, and overflow constraints.

- [x] **Step 1: Add source-level contract assertions**

Assert that `primaryCard` contains `parking-details`, the summary copy, both source fields wrapped through `escapeHtml`, and `官方未提供` fallback copy. Assert CSS contains `max-height:160px`, `overflow-y:auto`, `white-space:pre-wrap`, and `overflow-wrap:anywhere`.

- [x] **Step 2: Run JavaScript syntax verification**

Run: `node --check static/app.js`

Expected: exit code 0 with no syntax error.

- [x] **Step 3: Run focused frontend contracts**

Run: `python -m pytest tests/test_frontend_contract.py -q`

Expected: all frontend contract tests pass.

- [x] **Step 4: Run the complete test suite**

Run: `python -m pytest -q`

Expected: all tests pass with zero failures.
