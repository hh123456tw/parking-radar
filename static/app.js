/* 單頁互動：查詢固定 API，呈現精簡決策卡、Leaflet 與按需歷史圖。 */
const QUERY_TIMEOUT_MS = 20000;
const MIN_HISTORY_POINTS = 8;
const CLIENT_VERSION = "2";
const ANALYTICS_CONSENT_KEY = "parking_analytics_consent";
const ANALYTICS_ID_KEY = "parking_analytics_id";
const ANALYTICS_REQUIRE_CONSENT =
  document.body.dataset.analyticsRequireConsent === "1";
const districts = ["松山區","信義區","大安區","中山區","中正區","大同區","萬華區","文山區","南港區","內湖區","士林區","北投區"];

const map = L.map("map").setView([25.0478, 121.5319], 12);
const markerLayer = L.layerGroup().addTo(map);
const markerByLot = new Map();
let historyChart = null;
let activeRequestId = null;

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:"© OpenStreetMap contributors",
}).addTo(map);

const districtSelect = document.querySelector("#district");
districts.forEach(name => districtSelect.add(new Option(name, name)));
const localNow = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
  .toISOString().slice(0,16);
document.querySelector("#arrival-time").value = localNow;

// 分析來源依優先序判斷：share 參數、安裝模式、直接/同源造訪，其餘未知。
function analyticsSource() {
  if (new URLSearchParams(location.search).get("src") === "share") return "shared";
  if (matchMedia("(display-mode: standalone)").matches) return "installed_pwa";
  if (!document.referrer || new URL(document.referrer).origin === location.origin) return "direct";
  return "unknown";
}

function analyticsConsented() {
  return localStorage.getItem(ANALYTICS_CONSENT_KEY) === "accepted";
}

function ensureAnalyticsIdentity() {
  localStorage.setItem(ANALYTICS_CONSENT_KEY, "accepted");
  if (!localStorage.getItem(ANALYTICS_ID_KEY)) {
    localStorage.setItem(ANALYTICS_ID_KEY, crypto.randomUUID());
  }
}

// 查詢端點仍以標頭表示同意；未同意時回傳空物件，不帶任何分析資訊。
function analyticsHeaders() {
  if (!analyticsConsented()) return {};
  return {
    "X-Analytics-Consent": "1",
    "X-Analytics-Id": localStorage.getItem(ANALYTICS_ID_KEY),
    "X-Analytics-Source": analyticsSource(),
  };
}

// 事件優先走 sendBeacon；只有它不存在或回傳 false 時才退回 keepalive fetch，絕不阻塞頁面離開。
function sendAnalyticsEvent(payload) {
  const body = JSON.stringify(payload);
  const beaconSent = typeof navigator.sendBeacon === "function"
    && navigator.sendBeacon(
      "/api/analytics/events",
      new Blob([body], {type:"application/json"}));
  if (beaconSent) return;
  fetch("/api/analytics/events", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body,
    keepalive:true,
  }).catch(() => {});
}

// 同意下才送事件；未同意時直接略過，不發任何分析請求。
function track(payload) {
  if (!analyticsConsented()) return;
  sendAnalyticsEvent({
    ...payload,
    analytics_id:localStorage.getItem(ANALYTICS_ID_KEY),
    source:analyticsSource(),
  });
}

// 空位分群與後端一致，避免事件記錄精確空位數。
function availabilityBucket(spaces) {
  spaces = Math.max(0, Number(spaces) || 0);
  if (spaces === 0) return "0";
  if (spaces <= 3) return "1_3";
  if (spaces <= 10) return "4_10";
  return "11_plus";
}

// 單一委派處理導航、地圖優先、歷史與回饋；不取消、不延遲預設行為。
document.addEventListener("click", event => {
  const link = event.target.closest("a[data-navigation-rank]");
  if (link) {
    const rawMinutes = link.dataset.walkingMinutes;
    track({
      event_type:"navigation_clicked",
      request_id:activeRequestId,
      clicked_rank:Number(link.dataset.navigationRank),
      parking_lot_id:link.dataset.lotId || "",
      walking_minutes:rawMinutes === "" ? null : Number(rawMinutes),
      availability_bucket:link.dataset.availabilityBucket,
    });
    return;
  }
  const historyButton = event.target.closest("[data-history-lot]");
  if (historyButton) {
    track({
      event_type:"history_opened",
      request_id:activeRequestId,
      parking_lot_id:historyButton.dataset.historyLot,
    });
    loadHistory(historyButton.dataset.historyLot, historyButton.dataset.lotName);
    return;
  }
  const mapButton = event.target.closest("[data-map-lot]");
  if (mapButton) {
    const marker = markerByLot.get(String(mapButton.dataset.mapLot));
    if (!marker) return;
    track({
      event_type:"map_marker_clicked",
      request_id:activeRequestId,
      parking_lot_id:mapButton.dataset.mapLot,
      clicked_rank:Number(mapButton.dataset.mapRank || 0),
    });
    map.setView(marker.getLatLng(), 16);
    marker.openPopup();
    return;
  }
  const otherToggle = event.target.closest("#other-toggle");
  if (otherToggle) {
    const otherLots = document.querySelector("#other-lots");
    const willOpen = otherLots.hidden;
    otherLots.hidden = !willOpen;
    otherToggle.setAttribute("aria-expanded", String(willOpen));
    otherToggle.textContent = willOpen
      ? "收合其他場站" : otherToggle.dataset.closedLabel;
    return;
  }
  const feedbackButton = event.target.closest("[data-feedback]");
  if (feedbackButton) sendFeedback(feedbackButton.dataset.feedback);
});

function resetFeedback() {
  const section = document.querySelector("#parking-feedback");
  section.hidden = true;
  section.querySelectorAll("[data-feedback]").forEach(
    button => { button.disabled = false; });
  document.querySelector("#feedback-status").textContent = "";
}

async function sendFeedback(code) {
  const status = document.querySelector("#feedback-status");
  if (!analyticsConsented() || !activeRequestId) return;
  const buttons = [...document.querySelectorAll("[data-feedback]")];
  if (buttons.some(button => button.disabled)) return;
  // 送出前原子停用全部按鈕，避免快速連點送出兩次；只有失敗才恢復可用。
  buttons.forEach(button => { button.disabled = true; });
  try {
    const response = await fetch("/api/analytics/feedback", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({analytics_id:localStorage.getItem(ANALYTICS_ID_KEY),
                           request_id:activeRequestId, feedback_code:code}),
    });
    if (response.status !== 204) {
      const data = await response.json().catch(() => ({}));
      status.textContent = data.error || "回饋記錄失敗";
      buttons.forEach(button => { button.disabled = false; });
      return;
    }
    status.textContent = "已記錄你的回饋，感謝！";
  } catch {
    status.textContent = "回饋記錄失敗";
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function submitQuery(payload) {
  // 每次新查詢先清空 request_id，避免失敗或等待期間的點擊連到上一筆成功查詢。
  activeRequestId = null;
  hideLocationChoices();
  resetFeedback();
  showStatus("正在分析並確認官方停車資料…", "");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);
  try {
    const response = await fetch("/api/query", {
      method:"POST",
      headers:{
        "Content-Type":"application/json",
        "X-Client-Version":CLIENT_VERSION,
        ...analyticsHeaders(),
      },
      body:JSON.stringify(payload),
      signal:controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.fallback === "manual") document.querySelector("#manual-panel").open = true;
      throw new Error(data.error || "查詢失敗");
    }
    if (data.needs_location_choice) {
      document.querySelector("#result-content").hidden = true;
      renderLocationChoices(data);
      showStatus(`找到 ${data.location_choices.length} 個可能地點，請先確認。`, "");
      return;
    }
    document.querySelector("#result-content").hidden = false;
    renderSummary(data);
    renderCards(data);
    renderMap(data);
    resetHistory();
    // 只有終端成功回應才更新 request_id，供後續導航事件對應同一筆查詢。
    activeRequestId = data.request_id || null;
    document.querySelector("#parking-feedback").hidden =
      !(data.recommendations || []).length;
    showStatus(data.data_status === "stale"
      ? "分析完成；目前使用最後一次可取得的停車資料。"
      : "分析完成；資料來自臺北市官方即時資訊。", "success");
  } catch (error) {
    if (error.name === "AbortError") {
      document.querySelector("#manual-panel").open = true;
      throw new Error("分析超過 20 秒，請重試或改用手動查詢");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

document.querySelector("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await submitQuery({mode:"chat", message:document.querySelector("#message").value});
  } catch (error) {
    showStatus(error.message, "error");
  }
});

document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault();
  const arrival = new Date(document.querySelector("#arrival-time").value).toISOString();
  try {
    await submitQuery({
      mode:"manual",
      address:document.querySelector("#address").value,
      district:districtSelect.value,
      arrival_time:arrival,
    });
  } catch (error) {
    showStatus(error.message, "error");
  }
});

function showStatus(message, type) {
  const node = document.querySelector("#status");
  node.textContent = message;
  node.className = type;
}

function hideLocationChoices() {
  document.querySelector("#location-choice-section").hidden = true;
  document.querySelector("#location-choices").innerHTML = "";
}

function renderLocationChoices(data) {
  const section = document.querySelector("#location-choice-section");
  const choices = data.location_choices || [];
  section.hidden = false;
  track({event_type:"location_choice_shown", request_id:data.request_id});
  document.querySelector("#location-choices").innerHTML = choices.map(
    (choice, index) => `<button type="button" data-location-choice="${index}">
      <strong>${escapeHtml(choice.name)}</strong>
      <span>${escapeHtml(choice.address)}</span>
    </button>`).join("");

  document.querySelectorAll("[data-location-choice]").forEach(button => {
    button.addEventListener("click", async () => {
      const choice = choices[Number(button.dataset.locationChoice)];
      try {
        track({event_type:"location_choice_selected",
               request_id:data.request_id});
        await submitQuery({
          mode:"manual",
          address:choice.address,
          district:choice.district || "",
          arrival_time:data.arrival_time,
          destination_label:`${choice.name}（${choice.address}）`,
        });
      } catch (error) {
        showStatus(error.message, "error");
      }
    });
  });
}

function formatDistance(value) {
  if (value == null) return "行政區模式";
  return value < 1000 ? `${Math.round(value)} m` : `${(value / 1000).toFixed(1)} km`;
}

// 優先顯示停好車後的實際步行時間；外部路線失敗時誠實標示直線距離。
function formatProximity(lot) {
  if (lot.walking_duration_minutes != null && lot.walking_distance_m != null) {
    const minutes = Math.max(1, Math.round(lot.walking_duration_minutes));
    return `步行約 ${minutes} 分鐘・${formatDistance(lot.walking_distance_m)}`;
  }
  if (lot.distance_m == null) return "行政區模式";
  return `直線約 ${formatDistance(lot.distance_m)}`;
}

function districtStatus(score) {
  if (score == null) return "資料不足";
  if (score >= 95) return "停車地獄";
  if (score >= 80) return "很難停";
  if (score >= 60) return "開始擠";
  return "相對好停";
}

function renderSummary(data) {
  document.querySelector("#destination").textContent =
    data.destination?.display_address || "行政區查詢";
  const score = data.current.district_score;
  document.querySelector("#district-status").textContent = districtStatus(score);
  document.querySelector("#district-score").textContent =
    score == null ? "尚無有效分數" : `區域停車壓力 ${Math.round(score)} / 100`;
  document.querySelector("#valid-count").textContent =
    `${data.current.valid_lot_count} 座`;

  const officialTime = data.official_updated_at
    ? new Date(data.official_updated_at).toLocaleString("zh-TW") : "無資料";
  const collectedTime = data.collected_at
    ? new Date(data.collected_at).toLocaleString("zh-TW") : "無資料";
  document.querySelector("#official-updated-at").textContent =
    `官方資料時間：${officialTime}`;
  document.querySelector("#collected-at").textContent =
    `系統最後抓取：${collectedTime}`;

  const notice = document.querySelector("#data-notice");
  notice.hidden = !data.data_notice;
  notice.textContent = data.data_notice || "";
}

// 轉義官方文字，避免名稱、地址或原因被 innerHTML 當成標記執行。
function escapeHtml(value) {
  const entities = {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"};
  return String(value ?? "").replace(/[&<>"']/g, character => entities[character]);
}

// 顯示 API 已算好的值：修整後轉義，空白時用預設文字代替，前端絕不重算費率。
function displayValue(value, fallback = "官方未標示") {
  const text = String(value ?? "").trim();
  return text ? escapeHtml(text) : fallback;
}

function formatFullAddress(lot) {
  const address = (lot.address || "").replaceAll("台北市", "臺北市").trim();
  if (!address) return "地址資料未提供";
  if (address.startsWith("臺北市")) return address;
  if (lot.district && address.startsWith(lot.district)) return `臺北市${address}`;
  return `臺北市${lot.district || ""}${address}`;
}

function googleMapsUrl(lot) {
  const base = "https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=";
  if (lot.latitude != null && lot.longitude != null) {
    return `${base}${encodeURIComponent(`${lot.latitude},${lot.longitude}`)}`;
  }
  const address = formatFullAddress(lot);
  if (address === "地址資料未提供" && !lot.lot_name) return null;
  return `${base}${encodeURIComponent(`${lot.lot_name || ""} ${address}`.trim())}`;
}

function cheapestBadge(lot) {
  return lot.is_cheapest_hourly
    ? `<span class="cheapest-badge">每小時最便宜</span>` : "";
}

function primaryCard(lot, index) {
  const address = formatFullAddress(lot);
  const mapsUrl = googleMapsUrl(lot);
  const isBackup = lot.decision_status === "warning";
  const cardTone = isBackup ? "warning" : "recommended";
  const rankLabel = isBackup ? "備選" : "首選";
  const freePercent = Math.max(0, Math.min(100,
    lot.available_spaces / lot.total_spaces * 100));
  const primaryReason = lot.reasons?.[0] || "依目前空位與距離列入首選";
  const feeInfo = String(lot.fee_info || "").trim() || "官方未提供";
  const serviceTime = String(lot.service_time || "").trim() || "官方未提供";
  const metaLine = feeMetaLine(lot);
  const mapsLink = mapsUrl
    ? `<a class="primary-action" href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer" data-navigation-rank="${index + 1}" data-lot-id="${escapeHtml(lot.lot_id)}" data-walking-minutes="${lot.walking_duration_minutes ?? ""}" data-availability-bucket="${availabilityBucket(lot.available_spaces)}">開始導航</a>`
    : `<span class="primary-action disabled" aria-disabled="true">無地圖資料</span>`;

  return `<article class="parking-card ${cardTone}">
    <div class="card-top">
      <span class="rank-badge">${rankLabel} ${index + 1}</span>
      ${cheapestBadge(lot)}
      <span class="distance-label">${escapeHtml(formatProximity(lot))}</span>
    </div>
    <h3>${escapeHtml(lot.lot_name)}</h3>
    ${mapsUrl
      ? `<a class="parking-address" href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer" data-navigation-rank="${index + 1}" data-lot-id="${escapeHtml(lot.lot_id)}" data-walking-minutes="${lot.walking_duration_minutes ?? ""}" data-availability-bucket="${availabilityBucket(lot.available_spaces)}">${escapeHtml(address)}</a>`
      : `<span class="parking-address">${escapeHtml(address)}</span>`}
    <div class="capacity"><strong>${lot.available_spaces}</strong><span>格可停</span><small>共 ${lot.total_spaces} 格</small></div>
    <div class="capacity-bar" aria-label="空位比例 ${Math.round(freePercent)}%"><i style="width:${freePercent}%"></i></div>
    ${metaLine}
    <p class="decision-summary">${escapeHtml(primaryReason)}</p>
    <details class="parking-details">
      <summary>費率與營業時間</summary>
      <div class="parking-details-content">
        <div class="parking-detail-item">
          <strong>官方費率</strong>
          <p>${escapeHtml(feeInfo)}</p>
        </div>
        <div class="parking-detail-item">
          <strong>營業時間</strong>
          <p>${escapeHtml(serviceTime)}</p>
        </div>
      </div>
    </details>
    <div class="card-actions">
      ${mapsLink}
      <button class="secondary-action" type="button" data-history-lot="${escapeHtml(lot.lot_id)}" data-lot-name="${escapeHtml(lot.lot_name)}">查看空位趨勢</button>
    </div>
  </article>`;
}

// 第二、三選擇只保留比較所需資訊，避免手機畫面連續出現三張大型卡片。
function alternativeCard(lot, index) {
  const mapsUrl = googleMapsUrl(lot);
  const hourly = displayValue(lot.hourly_fee_label);
  const cap = displayValue(lot.daily_cap_label);
  const capText = cap === "官方未標示" ? "上限未標示" : `上限 ${cap}`;
  const facility = displayValue(lot.facility_type_label, "型態待確認");
  const mapsLink = mapsUrl
    ? `<a href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer" data-navigation-rank="${index + 1}" data-lot-id="${escapeHtml(lot.lot_id)}" data-walking-minutes="${lot.walking_duration_minutes ?? ""}" data-availability-bucket="${availabilityBucket(lot.available_spaces)}">導航</a>`
    : `<span class="muted">無地圖</span>`;
  return `<article class="alternative-card ${escapeHtml(lot.decision_status)}">
    <div class="alternative-heading">
      <span class="rank-badge">選擇 ${index + 1}</span>
      ${cheapestBadge(lot)}
    </div>
    <strong>${escapeHtml(lot.lot_name)}</strong>
    <span>${lot.available_spaces} / ${lot.total_spaces} 格可停</span>
    <span>${hourly}・${capText}</span>
    <span>${facility}・${escapeHtml(formatProximity(lot))}</span>
    ${mapsLink}
  </article>`;
}

// 首選卡用的決策元資料列：抵達日、時費、每日上限與場站型態，一律只顯示轉義後的值。
function feeMetaLine(lot) {
  const arrival = displayValue(lot.arrival_day_label);
  const hourly = displayValue(lot.hourly_fee_label);
  const cap = displayValue(lot.daily_cap_label);
  const facility = displayValue(lot.facility_type_label, "型態待確認");
  const capText = cap === "官方未標示" ? "上限官方未標示" : `上限 ${cap}`;
  const feeNote = lot.fee_note
    ? `<div class="fee-note">${displayValue(lot.fee_note)}</div>` : "";
  return `<div class="decision-meta">
    <span>抵達：${arrival}</span>
    <span>${hourly}</span>
    <span>${capText}</span>
    <span>${facility}</span>
  </div>${feeNote}`;
}

function compactLot(lot) {
  const mapsUrl = googleMapsUrl(lot);
  const mapAction = mapsUrl
    ? `<a href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer" data-navigation-rank="0" data-lot-id="${escapeHtml(lot.lot_id)}" data-walking-minutes="${lot.walking_duration_minutes ?? ""}" data-availability-bucket="${availabilityBucket(lot.available_spaces)}">導航</a>`
    : `<span class="muted">無地圖</span>`;
  return `<article class="compact-lot ${escapeHtml(lot.decision_status)}">
    <span class="compact-status">${escapeHtml(lot.decision_label)}${cheapestBadge(lot)}</span>
    <div><strong>${escapeHtml(lot.lot_name)}</strong><small>${lot.available_spaces} / ${lot.total_spaces} 格可停</small></div>
    ${compactMetaLine(lot)}
    <span>${escapeHtml(formatProximity(lot))}</span>
    ${mapAction}
  </article>`;
}

// 只比較「可以前往」且費率精確的場站；至少有兩種價格才產生最便宜標籤。
function cheapestHourlyFee(lots) {
  const exactPrices = lots
    .filter(lot => lot.decision_status === "recommended"
      && lot.fee_confidence === "exact"
      && lot.hourly_fee_value != null
      && Number.isFinite(Number(lot.hourly_fee_value)))
    .map(lot => Number(lot.hourly_fee_value));
  const distinctPrices = new Set(exactPrices);
  return distinctPrices.size >= 2 ? Math.min(...distinctPrices) : null;
}

// 緊湊列用的元資料行：名稱下方呈現抵達日、時費、每日上限與場站型態，值皆轉義。
function compactMetaLine(lot) {
  const arrival = displayValue(lot.arrival_day_label);
  const hourly = displayValue(lot.hourly_fee_label);
  const cap = displayValue(lot.daily_cap_label);
  const facility = displayValue(lot.facility_type_label, "型態待確認");
  const capText = cap === "官方未標示" ? "上限官方未標示" : `上限 ${cap}`;
  const feeNote = lot.fee_note
    ? `<div class="fee-note">${displayValue(lot.fee_note)}</div>` : "";
  return `<div class="compact-meta">
    <span>抵達：${arrival}</span>
    <span>${hourly}</span>
    <span>${capText}</span>
    <span>${facility}</span>
  </div>${feeNote}`;
}

function renderCards(data) {
  const rawRecommendations = data.recommendations || [];
  const rawOtherRecommended = data.other_recommended || [];
  const rawWarning = data.warning || [];
  const cheapestFee = cheapestHourlyFee([
    ...rawRecommendations, ...rawOtherRecommended,
  ]);
  const decoratePrice = lot => ({
    ...lot,
    is_cheapest_hourly:cheapestFee !== null
      && lot.decision_status === "recommended"
      && lot.fee_confidence === "exact"
      && Number(lot.hourly_fee_value) === cheapestFee,
  });
  const recommendations = rawRecommendations.map(decoratePrice);
  const otherRecommended = rawOtherRecommended.map(decoratePrice);
  const warning = rawWarning.map(decoratePrice);
  const otherLots = [...otherRecommended, ...warning];
  document.querySelector("#recommendations").innerHTML = recommendations.length
    ? `${primaryCard(recommendations[0], 0)}
       ${recommendations.length > 1 ? `<div class="alternative-choices">
         <h3>另外 ${recommendations.length - 1} 個選擇</h3>
         ${recommendations.slice(1).map((lot, index) =>
           alternativeCard(lot, index + 1)).join("")}
       </div>` : ""}`
    : `<p class="group-empty">附近目前沒有可以前往或可供備選的場站。</p>`;

  const excludedSummary = document.querySelector("#excluded-summary");
  const excludedCount = Number(data.excluded_count || 0);
  excludedSummary.hidden = excludedCount === 0;
  excludedSummary.textContent = excludedCount
    ? `已排除 ${excludedCount} 座剩餘空位過少的高風險場站。` : "";

  const otherSection = document.querySelector("#other-section");
  otherSection.hidden = otherLots.length === 0;
  const toggle = document.querySelector("#other-toggle");
  const otherList = document.querySelector("#other-lots");
  const cheapestIsHidden = otherRecommended.some(lot => lot.is_cheapest_hourly);
  toggle.setAttribute("aria-expanded", "false");
  toggle.dataset.closedLabel = `查看其他 ${otherLots.length} 座場站${
    cheapestIsHidden ? "（含每小時最便宜）" : ""}`;
  toggle.textContent = toggle.dataset.closedLabel;
  otherList.hidden = true;
  const safeCount = Number(data.recommended_count || 0);
  document.querySelector("#other-title").textContent = otherRecommended.length
    ? "其他可以前往" : "附近備選";
  document.querySelector("#other-note").textContent = otherRecommended.length
    ? `附近共有 ${safeCount} 座可以前往，以下為其餘安全場站。`
    : "附近安全場站不足，以下場站抵達前請再次確認空位。";
  otherList.innerHTML = otherLots.map(compactLot).join("");
}

function markerPopup(lot) {
  return `<strong>${escapeHtml(lot.lot_name)}</strong><br>剩餘 ${lot.available_spaces} / ${lot.total_spaces} 格<br>${escapeHtml(formatProximity(lot))}`;
}

function renderMap(data) {
  markerLayer.clearLayers();
  markerByLot.clear();
  const focusPoints = [];
  const fallbackPoints = [];

  if (data.destination) {
    const destination = L.marker([data.destination.latitude, data.destination.longitude])
      .bindPopup("目的地")
      .bindTooltip("目的地", {permanent:true, direction:"top", className:"destination-label"})
      .addTo(markerLayer);
    focusPoints.push(destination.getLatLng());
  }

  (data.recommendations || []).forEach((lot, index) => {
    if (lot.latitude == null || lot.longitude == null) return;
    const color = lot.decision_status === "warning" ? "#f2c94c" : "#36c98f";
    const marker = L.circleMarker([lot.latitude, lot.longitude], {
      radius:14, color:"#ffffff", weight:3,
      fillColor:color, fillOpacity:1,
    }).bindPopup(markerPopup(lot))
      .bindTooltip(String(index + 1), {
        permanent:true, direction:"center", className:"marker-rank",
      }).addTo(markerLayer);
    markerByLot.set(String(lot.lot_id), marker);
    focusPoints.push(marker.getLatLng());
  });

  [...(data.other_recommended || []), ...(data.warning || [])].forEach(lot => {
    if (lot.latitude == null || lot.longitude == null) return;
    const color = lot.decision_status === "warning" ? "#f2c94c" : "#36c98f";
    const marker = L.circleMarker([lot.latitude, lot.longitude], {
      radius:7, color, weight:2, fillColor:color, fillOpacity:.72,
    }).bindPopup(markerPopup(lot)).addTo(markerLayer);
    markerByLot.set(String(lot.lot_id), marker);
    fallbackPoints.push(marker.getLatLng());
  });

  const points = focusPoints.length > (data.destination ? 1 : 0)
    ? focusPoints : focusPoints.concat(fallbackPoints);
  if (points.length) map.fitBounds(points, {padding:[42,42], maxZoom:16});

  const priorities = data.recommendations || [];
  document.querySelector("#map-priorities").innerHTML = priorities.length
    ? priorities.map((lot, index) => `<li><button type="button" data-map-lot="${escapeHtml(lot.lot_id)}" data-map-rank="${index + 1}"><span>${index + 1}</span><strong>${escapeHtml(lot.lot_name)}</strong><small>${lot.available_spaces} 格可停・${escapeHtml(formatProximity(lot))}</small></button></li>`).join("")
    : `<li class="map-empty">目前沒有首選位置</li>`;
}

function resetHistory() {
  document.querySelector("#history-section").hidden = true;
  document.querySelector("#history-chart-shell").hidden = true;
  if (historyChart) {
    historyChart.destroy();
    historyChart = null;
  }
}

async function loadHistory(lotId, lotName) {
  const section = document.querySelector("#history-section");
  const shell = document.querySelector("#history-chart-shell");
  const note = document.querySelector("#history-note");
  section.hidden = false;
  shell.hidden = true;
  document.querySelector("#history-title").textContent = `${lotName}空位變化`;
  note.textContent = "正在載入歷史資料…";
  try {
    const response = await fetch(`/api/parking/${encodeURIComponent(lotId)}/history`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "暫時無法取得歷史資料");
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
    if (data.points.length < MIN_HISTORY_POINTS) {
      note.textContent = `歷史資料累積中，目前 ${data.points.length} 筆；累積 ${MIN_HISTORY_POINTS} 筆後顯示趨勢。`;
      return;
    }

    shell.hidden = false;
    note.textContent = `最近七天共 ${data.points.length} 筆有效資料；僅供趨勢參考。`;
    const labels = data.points.map(point =>
      new Date(point.captured_at).toLocaleString("zh-TW", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}));
    const values = data.points.map(point => point.available_spaces);
    historyChart = new Chart(document.querySelector("#history-chart"), {
      type:"line",
      data:{labels, datasets:[{
        label:"剩餘汽車位", data:values,
        borderColor:"#ff9a57", backgroundColor:"#ff9a5726",
        fill:true, tension:.25, pointRadius:2,
      }]},
      options:{
        responsive:true, maintainAspectRatio:false,
        plugins:{legend:{labels:{color:"#cbd5df"}}},
        scales:{
          x:{ticks:{color:"#8f9dab", maxTicksLimit:8}, grid:{color:"#29333f"}},
          y:{beginAtZero:true, ticks:{color:"#8f9dab"}, grid:{color:"#29333f"}},
        },
      },
    });
  } catch (error) {
    note.textContent = error.message;
  }
}

// 語音輸入：同時偵測 Safari 的兩種 Web Speech API 前綴，結果只填入 #message，絕不自動送出。
const SpeechRecognitionApi = window.SpeechRecognition || window.webkitSpeechRecognition;

function setupVoiceInput() {
  const voiceButton = document.querySelector("#voice-input");
  const input = document.querySelector("#message");
  if (!voiceButton || !input || !SpeechRecognitionApi) return;
  voiceButton.hidden = false;
  const recognition = new SpeechRecognitionApi();
  recognition.lang = "zh-TW";
  recognition.continuous = false;
  // iOS Safari 手動 stop() 不一定交付 final；先接收中間結果，說話時就能保存可用文字。
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  let isListening = false;
  let isStarting = false;
  let isStopping = false;
  let receivedResult = false;
  let receivedError = false;

  // 監聽中切換成紅色與可見「停止」，結束後回復「語音」與 aria-pressed=false；
  // 找不到 .voice-label 時仍維持按鈕狀態切換，不中斷正常文字更新。
  function setListening(listening) {
    isListening = listening;
    voiceButton.classList.toggle("listening", listening);
    voiceButton.setAttribute("aria-pressed", String(listening));
    voiceButton.setAttribute("aria-label",
      listening ? "停止語音輸入" : "使用語音輸入目的地");
    const label = voiceButton.querySelector(".voice-label");
    if (label) label.textContent = listening ? "停止" : "語音";
  }

  // Safari 在 stop() 後仍可能需要時間整理最終文字；收尾期間禁止重新開始，避免舊 onend 干擾新錄音。
  function setProcessing(processing) {
    voiceButton.disabled = processing;
    voiceButton.setAttribute("aria-label",
      processing ? "正在辨識語音" : "使用語音輸入目的地");
    const label = voiceButton.querySelector(".voice-label");
    if (label && processing) label.textContent = "辨識中";
  }

  // 手動停止與自然說完共用同一條路徑，明確要求 Safari 停止收音並整理目前結果。
  function requestVoiceStop() {
    if (!isListening || isStopping) return;
    isStopping = true;
    setListening(false);
    setProcessing(true);
    if (receivedResult) {
      showStatus("已填入語音結果，請確認後按分析", "");
    } else if (!receivedError) {
      showStatus("正在辨識語音，請稍候", "");
    }
    recognition.stop();
  }

  // 只有使用者主動點擊才開始；再點一次就停止，避免背景持續收音。
  // onstart 尚未觸發前忽略重複點擊，避免快速連點呼叫兩次 start()。
  voiceButton.addEventListener("click", () => {
    if (isListening) {
      requestVoiceStop();
      return;
    }
    if (isStarting || isStopping) return;
    isStarting = true;
    receivedResult = false;
    receivedError = false;
    try {
      recognition.start();
    } catch {
      isStarting = false;
      showStatus("語音輸入失敗，請改用鍵盤輸入", "error");
    }
  });

  recognition.onstart = () => {
    isStarting = false;
    setListening(true);
    showStatus("正在聆聽，請說出目的地", "");
  };

  // continuous=false 不代表各版 Safari 都會主動釋放麥克風；偵測到說話結束時明確 stop()。
  recognition.onspeechend = () => {
    requestVoiceStop();
  };

  // 辨識結果只填入目的地欄位並聚焦，由使用者確認後自己按「分析」；
  // 空轉錄結果不算成功，不覆寫輸入也不顯示成功訊息。
  recognition.onresult = event => {
    const results = event.results;
    if (!results?.length) return;
    let transcript = "";
    for (let index = 0; index < results.length; index += 1) {
      transcript += results[index]?.[0]?.transcript || "";
    }
    transcript = transcript.trim();
    if (transcript) {
      receivedResult = true;
      input.value = transcript;
      input.focus();
      showStatus("已填入語音結果，請確認後按分析", "");
    }
  };

  recognition.onerror = event => {
    receivedError = true;
    const code = event.error;
    let message;
    if (code === "not-allowed" || code === "service-not-allowed") {
      message = "請允許 Safari 使用麥克風";
    } else if (code === "no-speech") {
      message = "沒有聽到語音，請再試一次";
    } else if (code === "network") {
      message = "語音服務暫時無法連線";
    } else {
      message = "語音輸入失敗，請改用鍵盤輸入";
    }
    showStatus(message, "error");
  };

  recognition.onend = () => {
    isStarting = false;
    isStopping = false;
    setProcessing(false);
    setListening(false);
    if (!receivedResult && !receivedError) {
      showStatus("沒有取得語音文字，請再試一次", "error");
    }
  };
}

// PWA 安裝與分析同意：註冊服務器攔截安裝事件，但只在使用者點擊按鈕後才呼叫 prompt。
document.addEventListener("DOMContentLoaded", () => {
  setupVoiceInput();
  const consentSection = document.querySelector("#analytics-consent");
  const acceptButton = document.querySelector("#analytics-accept");
  const declineButton = document.querySelector("#analytics-decline");
  const changeButton = document.querySelector("#analytics-choice");
  let pwaOpenedRecorded = false;

  // 團隊測試模式不顯示選擇介面，但沿用同一套去識別 UUID 與事件格式。
  if (!ANALYTICS_REQUIRE_CONSENT) {
    ensureAnalyticsIdentity();
  }

  // 同意後每次頁面載入最多記錄一次；pwa_opened 只帶來源與本機 UUID，不含 request_id 或網址。
  function recordPwaOpenedOnce() {
    if (pwaOpenedRecorded || !analyticsConsented()) return;
    pwaOpenedRecorded = true;
    sendAnalyticsEvent({
      event_type:"pwa_opened",
      analytics_id:localStorage.getItem(ANALYTICS_ID_KEY),
      source:analyticsSource(),
    });
  }

  if (consentSection && acceptButton && declineButton) {
    // 只有完全沒有選擇紀錄才顯示橫幅；已同意或已拒絕的本頁載入不再打擾。
    if (localStorage.getItem(ANALYTICS_CONSENT_KEY) === null) {
      consentSection.hidden = false;
    }
    acceptButton.addEventListener("click", () => {
      // 同一台裝置只建立一次 UUID；之後重開選擇不會更換身份。
      ensureAnalyticsIdentity();
      consentSection.hidden = true;
      recordPwaOpenedOnce();
    });
    declineButton.addEventListener("click", () => {
      // 拒絕就固定寫入 declined，並刪除 UUID；之後不再送出任何分析請求。
      localStorage.setItem(ANALYTICS_CONSENT_KEY, "declined");
      localStorage.removeItem(ANALYTICS_ID_KEY);
      consentSection.hidden = true;
    });
  }
  if (changeButton && consentSection) {
    changeButton.addEventListener("click", () => {
      consentSection.hidden = false;
    });
  }
  recordPwaOpenedOnce();

  if (!("serviceWorker" in navigator)) return;
  // 讓 /static/sw.js 管理整個站台；伺服器未回傳 Service-Worker-Allowed 時退回預設範圍。
  navigator.serviceWorker.register("/static/sw.js?v=decision-ui-v1", {scope:"/"})
    .catch(() => navigator.serviceWorker.register("/static/sw.js?v=decision-ui-v1"));

  let deferredPrompt = null;
  const installButton = document.querySelector("#install-app");
  const iosHint = document.querySelector("#ios-install-hint");

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    deferredPrompt = event;
    if (installButton) installButton.hidden = false;
  });

  if (installButton) {
    installButton.addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      installButton.hidden = true;
    });
  }

  // iOS Safari 不會觸發 beforeinstallprompt，改用分享功能加入主畫面。
  const iosSafari = /iphone|ipad|ipod/i.test(navigator.userAgent)
    && /safari/i.test(navigator.userAgent)
    && !/crios|fxios|opios|edgios/i.test(navigator.userAgent);
  if (iosHint && iosSafari && !window.matchMedia("(display-mode: standalone)").matches) {
    iosHint.hidden = false;
  }
});
