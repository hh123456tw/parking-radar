/* 單頁互動：查詢固定 API，呈現精簡決策卡、Leaflet 與按需歷史圖。 */
const QUERY_TIMEOUT_MS = 20000;
const MIN_HISTORY_POINTS = 8;
const districts = ["松山區","信義區","大安區","中山區","中正區","大同區","萬華區","文山區","南港區","內湖區","士林區","北投區"];

const map = L.map("map").setView([25.0478, 121.5319], 12);
const markerLayer = L.layerGroup().addTo(map);
const markerByLot = new Map();
let historyChart = null;

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:"© OpenStreetMap contributors",
}).addTo(map);

const districtSelect = document.querySelector("#district");
districts.forEach(name => districtSelect.add(new Option(name, name)));
const localNow = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
  .toISOString().slice(0,16);
document.querySelector("#arrival-time").value = localNow;

async function submitQuery(payload) {
  showStatus("正在分析並確認官方停車資料…", "");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);
  try {
    const response = await fetch("/api/query", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload),
      signal:controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.fallback === "manual") document.querySelector("#manual-panel").open = true;
      throw new Error(data.error || "查詢失敗");
    }
    renderSummary(data);
    renderCards(data);
    renderMap(data);
    resetHistory();
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

function formatDistance(value) {
  if (value == null) return "行政區模式";
  return value < 1000 ? `${Math.round(value)} m` : `${(value / 1000).toFixed(1)} km`;
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

function formatFullAddress(lot) {
  const address = (lot.address || "").replaceAll("台北市", "臺北市").trim();
  if (!address) return "地址資料未提供";
  if (address.startsWith("臺北市")) return address;
  if (lot.district && address.startsWith(lot.district)) return `臺北市${address}`;
  return `臺北市${lot.district || ""}${address}`;
}

function googleMapsUrl(lot) {
  const base = "https://www.google.com/maps/search/?api=1&query=";
  if (lot.latitude != null && lot.longitude != null) {
    return `${base}${encodeURIComponent(`${lot.latitude},${lot.longitude}`)}`;
  }
  const address = formatFullAddress(lot);
  if (address === "地址資料未提供" && !lot.lot_name) return null;
  return `${base}${encodeURIComponent(`${lot.lot_name || ""} ${address}`.trim())}`;
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
  const mapsLink = mapsUrl
    ? `<a class="primary-action" href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer">開啟 Google 地圖</a>`
    : `<span class="primary-action disabled" aria-disabled="true">無地圖資料</span>`;

  return `<article class="parking-card ${cardTone}">
    <div class="card-top">
      <span class="rank-badge">${rankLabel} ${index + 1}</span>
      <span class="distance-label">${escapeHtml(formatDistance(lot.distance_m))}</span>
    </div>
    <h3>${escapeHtml(lot.lot_name)}</h3>
    ${mapsUrl
      ? `<a class="parking-address" href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(address)}</a>`
      : `<span class="parking-address">${escapeHtml(address)}</span>`}
    <div class="capacity"><strong>${lot.available_spaces}</strong><span>格可停</span><small>共 ${lot.total_spaces} 格</small></div>
    <div class="capacity-bar" aria-label="空位比例 ${Math.round(freePercent)}%"><i style="width:${freePercent}%"></i></div>
    <p class="decision-summary">${escapeHtml(primaryReason)}</p>
    <div class="card-actions">
      ${mapsLink}
      <button class="secondary-action" type="button" data-history-lot="${escapeHtml(lot.lot_id)}" data-lot-name="${escapeHtml(lot.lot_name)}">查看空位趨勢</button>
    </div>
  </article>`;
}

function compactLot(lot) {
  const mapsUrl = googleMapsUrl(lot);
  const mapAction = mapsUrl
    ? `<a href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer">Google 地圖</a>`
    : `<span class="muted">無地圖</span>`;
  return `<article class="compact-lot ${escapeHtml(lot.decision_status)}">
    <span class="compact-status">${escapeHtml(lot.decision_label)}</span>
    <div><strong>${escapeHtml(lot.lot_name)}</strong><small>${lot.available_spaces} / ${lot.total_spaces} 格可停</small></div>
    <span>${escapeHtml(formatDistance(lot.distance_m))}</span>
    ${mapAction}
  </article>`;
}

function renderCards(data) {
  const recommendations = data.recommendations || [];
  const otherLots = [...(data.warning || []), ...(data.avoid || [])];
  document.querySelector("#recommendations").innerHTML = recommendations.length
    ? recommendations.map(primaryCard).join("")
    : `<p class="group-empty">目前沒有低風險首選，請查看其他附近場站。</p>`;

  const otherSection = document.querySelector("#other-section");
  otherSection.hidden = otherLots.length === 0;
  document.querySelector("#other-lots").innerHTML = otherLots.map(compactLot).join("");

  document.querySelectorAll("[data-history-lot]").forEach(button => {
    button.addEventListener("click", () => loadHistory(
      button.dataset.historyLot, button.dataset.lotName));
  });
}

function markerPopup(lot) {
  return `<strong>${escapeHtml(lot.lot_name)}</strong><br>剩餘 ${lot.available_spaces} / ${lot.total_spaces} 格<br>${escapeHtml(formatDistance(lot.distance_m))}`;
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

  [...(data.warning || []), ...(data.avoid || [])].forEach(lot => {
    if (lot.latitude == null || lot.longitude == null) return;
    const color = lot.decision_status === "avoid" ? "#ff5d66" : "#f2c94c";
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
    ? priorities.map((lot, index) => `<li><button type="button" data-map-lot="${escapeHtml(lot.lot_id)}"><span>${index + 1}</span><strong>${escapeHtml(lot.lot_name)}</strong><small>${lot.available_spaces} 格可停・${escapeHtml(formatDistance(lot.distance_m))}</small></button></li>`).join("")
    : `<li class="map-empty">目前沒有首選位置</li>`;

  document.querySelectorAll("[data-map-lot]").forEach(button => {
    button.addEventListener("click", () => {
      const marker = markerByLot.get(String(button.dataset.mapLot));
      if (!marker) return;
      map.setView(marker.getLatLng(), 16);
      marker.openPopup();
    });
  });
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
