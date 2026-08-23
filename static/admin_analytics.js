/* 唯讀管理儀表板：只在載入與點擊日期範圍時更新，不做輪詢。 */
"use strict";

const RANGE_LABELS = { today: "今日", "7d": "7 天", "30d": "30 天" };
const SYSTEM_KEYS = [
  "application", "database", "official_data", "collector", "metadata",
  "load", "memory", "disk", "deploy", "analytics",
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

function setNote(noteId, text, isError = false) {
  const note = document.getElementById(noteId);
  note.textContent = text || "";
  note.hidden = !text;
  note.classList.toggle("error", isError);
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
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

function renderKpiCards(summary) {
  const cards = document.getElementById("kpi-cards");
  cards.textContent = "";
  const kpis = [
    ["完成查詢數", numberText(summary.completed_queries), ""],
    ["查詢成功率", percentText(summary.query_success_rate),
      summary.degraded_queries ? `降級 ${summary.degraded_queries} 次` : ""],
    ["導航點擊率", percentText(summary.navigation_click_rate),
      summary.navigation_provisional ? "暫估（觀察窗未結束）" : ""],
    ["回應中位數", summary.response_median_ms === null ? "—"
      : `${numberText(summary.response_median_ms)} ms`, ""],
    ["回應 P95", summary.response_p95_ms === null ? "—"
      : `${numberText(summary.response_p95_ms)} ms`, ""],
    ["匿名查詢裝置", numberText(summary.anonymous_query_devices), ""],
    ["30 天重複使用率", percentText(summary.repeat_use_rate), ""],
  ];
  for (const [label, value, detail] of kpis) {
    const card = el("article", "kpi-card");
    card.appendChild(el("span", "kpi-label", label));
    card.appendChild(el("strong", "kpi-value", value));
    if (detail) card.appendChild(el("small", "kpi-detail", detail));
    cards.appendChild(card);
  }
}

function renderTable(bodyId, rows, columns) {
  const body = document.getElementById(bodyId);
  body.textContent = "";
  if (!rows || rows.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = columns;
    cell.textContent = "本時段沒有資料";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      tr.appendChild(el("td", "", value));
    }
    body.appendChild(tr);
  }
}

function renderDiagnostics(summary) {
  const counts = summary.click_rank_counts || {};
  const clickTotal = Object.values(counts).reduce((a, b) => a + b, 0);
  const clickRows = [1, 2, 3].map((rank) => {
    const count = counts[String(rank)] || 0;
    const share = clickTotal ? `${((count / clickTotal) * 100).toFixed(1)}%`
      : "—";
    return [`第 ${rank} 名`, String(count), share];
  });
  renderTable("click-rank-body", clickRows, 3);
  renderTable("district-body", (summary.districts || []).map(
    (row) => [row.district, String(row.devices)]), 2);
  renderTable("outcome-body",
    Object.entries(summary.outcome_code_counts || {}), 2);
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
    renderKpiCards(data.summary);
    renderDiagnostics(data.summary);
    if (!data.analytics_enabled) {
      setNote("analytics-note", "匿名分析未設定：缺少 HMAC 秘密，統計保持空白。");
    } else if (summaryIsEmpty(data.summary)) {
      setNote("analytics-note", "本時段沒有資料。");
    }
  } catch (error) {
    setNote("analytics-note", "指標載入失敗，請稍後再試。", true);
  }
}

function setActiveRange(range) {
  document.querySelectorAll(".range-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.range === range);
  });
  document.getElementById("range-label").textContent =
    RANGE_LABELS[range] || range;
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
