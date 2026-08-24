/* 唯讀管理儀表板：只在載入與點擊日期範圍時更新，不做輪詢。 */
"use strict";

const RANGE_LABELS = { today: "今日", "7d": "7 天", "30d": "30 天" };
const SYSTEM_KEYS = [
  "application", "database", "official_data", "collector", "metadata",
  "load", "memory", "disk", "deploy", "analytics",
];
const OUTCOME_LABELS = {
  success: "成功",
  degraded_gemini_fallback: "降級：AI 備援",
  degraded_stale_data: "降級：資料過舊",
  failed_validation: "失敗：輸入驗證",
  failed_geocode: "失敗：地址解析",
  failed_no_candidates: "失敗：無候選",
  failed_database: "失敗：資料庫",
  failed_internal: "失敗：內部錯誤",
};
const FEEDBACK_LABELS = {
  found_space: "有，找到車位",
  full_on_arrival: "到場已滿",
  did_not_go: "沒有前往",
};
const STAGE_KEYS = [
  "parse_ms", "geocode_ms", "freshness_ms", "database_ms", "walking_ms",
];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function numberText(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function percentText(value) {
  return value === null || value === undefined
    ? "—"
    : `${Number(value).toFixed(1)}%`;
}

function msText(value) {
  return value === null || value === undefined ? "—" : `${value} ms`;
}

function setNote(noteId, text, isError = false) {
  const note = document.getElementById(noteId);
  note.textContent = text || "";
  note.hidden = !text;
  note.classList.toggle("error", isError);
}

async function fetchJson(url) {
  const separator = url.includes("?") ? "&" : "?";
  const freshUrl = `${url}${separator}_=${Date.now()}`;
  const response = await fetch(freshUrl, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function renderSystemStrip(status) {
  const strip = document.getElementById("system-strip");
  strip.textContent = "";
  for (const key of SYSTEM_KEYS) {
    const item = status[key] || {};
    const pill = el("div", `status-pill tone-${item.tone || "gray"}`);
    pill.appendChild(el("span", "status-label", item.label || key));
    pill.appendChild(el("strong", "status-value", numberText(item.value)));
    pill.title = item.detail || "";
    strip.appendChild(pill);
  }
}

function renderEmptyRow(bodyId, columns, message) {
  const body = document.getElementById(bodyId);
  body.textContent = "";
  const row = document.createElement("tr");
  const cell = el("td", "empty-cell", message);
  cell.colSpan = columns;
  row.appendChild(cell);
  body.appendChild(row);
}

function renderTableRows(bodyId, rows, columns, emptyMessage) {
  if (!rows || rows.length === 0) {
    renderEmptyRow(bodyId, columns, emptyMessage);
    return;
  }
  const body = document.getElementById(bodyId);
  body.textContent = "";
  for (const values of rows) {
    const tr = document.createElement("tr");
    for (const value of values) {
      tr.appendChild(el("td", "", value));
    }
    body.appendChild(tr);
  }
}

function foundSpaceRate(insights) {
  const total = insights && insights.funnel ? insights.funnel.feedback : 0;
  const found = insights && insights.feedback
    ? insights.feedback.found_space : 0;
  if (!total) return { value: "—", detail: "尚無回饋" };
  return { value: `${((found / total) * 100).toFixed(1)}%`, detail: "" };
}

function renderKpiCards(summary, insights) {
  const cards = document.getElementById("kpi-cards");
  cards.textContent = "";
  const median = summary.response_median_ms;
  const p95 = summary.response_p95_ms;
  const feedback = foundSpaceRate(insights);
  const kpis = [
    ["完成查詢", numberText(summary.completed_queries), ""],
    ["成功率", percentText(summary.query_success_rate),
      summary.degraded_queries ? `降級 ${summary.degraded_queries} 次` : ""],
    ["回應中位數 / P95",
      `${msText(median)} / ${msText(p95)}`, ""],
    ["導航點擊率", percentText(summary.navigation_click_rate),
      summary.navigation_provisional ? "暫估（觀察窗未結束）" : ""],
    ["找到車位回饋率", feedback.value, feedback.detail],
    ["匿名測試裝置", numberText(summary.anonymous_query_devices), ""],
  ];
  for (const [label, value, detail] of kpis) {
    const card = el("article", "kpi-card");
    card.appendChild(el("span", "kpi-label", label));
    card.appendChild(el("strong", "kpi-value", value));
    if (detail) card.appendChild(el("small", "kpi-detail", detail));
    cards.appendChild(card);
  }
}

function renderFunnel(funnel) {
  const list = document.getElementById("funnel");
  list.textContent = "";
  const steps = [
    ["完成查詢", funnel.completed],
    ["地點選擇", funnel.location_choices],
    ["導航點擊", funnel.navigations],
    ["回饋", funnel.feedback],
  ];
  for (const [label, count] of steps) {
    const item = el("li", "funnel-step");
    item.appendChild(el("span", "funnel-label", label));
    item.appendChild(el("strong", "funnel-count", numberText(count)));
    const share = funnel.completed
      ? `${((count / funnel.completed) * 100).toFixed(0)}%` : "—";
    item.appendChild(el("small", "funnel-share", `佔完成 ${share}`));
    list.appendChild(item);
  }
}

function renderUserDestinations(insights) {
  renderTableRows("district-body", (insights.districts || []).map(
    (row) => [row.district, numberText(row.queries)]), 2,
  "尚無行政區資料，請完成一次新查詢");
  renderTableRows("destination-body", (insights.destinations || []).map(
    (row) => [row.destination, numberText(row.queries)]), 2,
  "尚無目的地資料，請完成一次新查詢");
  renderTableRows("lot-body", (insights.lots || []).map(
    (row) => [row.lot_name || "—", numberText(row.navigations),
      row.rank === null || row.rank === undefined
        ? "—" : `第 ${row.rank} 名`]), 3,
  "尚無導航點擊");
}

function renderStageTimings(timings) {
  timings = timings || {};
  const hasAny = STAGE_KEYS.some((key) => timings[key] !== null
    && timings[key] !== undefined);
  if (!hasAny) {
    renderEmptyRow("stage-body", STAGE_KEYS.length,
      "尚無分段耗時資料，請完成一次新查詢");
    return;
  }
  const body = document.getElementById("stage-body");
  body.textContent = "";
  const tr = document.createElement("tr");
  for (const key of STAGE_KEYS) {
    tr.appendChild(el("td", "", msText(timings[key])));
  }
  body.appendChild(tr);
}

function renderImprovements(summary, insights) {
  renderStageTimings(insights.stage_timings);
  const failures = Object.entries(summary.outcome_code_counts || {})
    .filter(([code]) => code !== "success")
    .map(([code, count]) => [OUTCOME_LABELS[code] || code, numberText(count)]);
  renderTableRows("outcome-body", failures, 2, "尚無失敗或降級記錄");
  const choices = insights.location_choice_counts || {};
  renderTableRows("location-choice-body", [
    ["出現", numberText(choices.shown)],
    ["選擇", numberText(choices.selected)],
  ], 2, "尚無地點選擇記錄");
}

function formatTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function renderRecentQueries(insights) {
  const rows = insights.recent_queries || [];
  if (!rows.length) {
    renderEmptyRow("recent-body", 7, "尚無查詢資料，請完成一次新查詢");
    return;
  }
  const body = document.getElementById("recent-body");
  body.textContent = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "", formatTime(row.occurred_at)));
    tr.appendChild(el("td", "", row.query || "（14 天後已清除）"));
    tr.appendChild(el("td", "", row.district || "—"));
    tr.appendChild(el("td", "",
      OUTCOME_LABELS[row.outcome_code] || row.outcome_code || "—"));
    tr.appendChild(el("td", "", msText(row.total_ms)));
    tr.appendChild(el("td", "", row.lot_name || "—"));
    tr.appendChild(el("td", "",
      FEEDBACK_LABELS[row.feedback_code] || "—"));
    body.appendChild(tr);
  }
}

function summaryIsEmpty(summary) {
  return summary.completed_queries === 0
    && summary.query_success_rate === null
    && summary.navigation_click_rate === null;
}

async function loadStatus() {
  try {
    renderSystemStrip(await fetchJson("/admin/api/status"));
    setNote("status-note", "");
  } catch (error) {
    setNote("status-note", "系統狀態載入失敗，請稍後再試。", true);
  }
}

async function loadAnalytics(range) {
  setNote("analytics-note", "");
  try {
    const data = await fetchJson(
      `/admin/api/analytics?range=${encodeURIComponent(range)}`);
    renderKpiCards(data.summary, data.insights);
    renderFunnel(data.insights.funnel);
    renderUserDestinations(data.insights);
    renderImprovements(data.summary, data.insights);
    renderRecentQueries(data.insights);
    if (!data.analytics_enabled) {
      setNote("analytics-note", "匿名分析未設定：缺少 HMAC 秘密，統計保持空白。");
    } else if (summaryIsEmpty(data.summary)) {
      setNote("analytics-note", "尚無任何資料，請先完成一次新查詢");
    }
  } catch (error) {
    setNote("analytics-note", "指標載入失敗，請稍後再試。", true);
  }
}

function setActiveRange(range) {
  document.querySelectorAll(".range-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.range === range);
  });
  const rangeLabel = document.getElementById("range-label");
  if (rangeLabel) rangeLabel.textContent = RANGE_LABELS[range] || range;
}

function init() {
  setActiveRange("today");
  loadStatus();
  loadAnalytics("today");
  document.querySelectorAll(".range-button").forEach((button) => {
    button.addEventListener("click", () => {
      const range = button.dataset.range;
      setActiveRange(range);
      loadAnalytics(range);
    });
  });
}

document.addEventListener("DOMContentLoaded", init);
