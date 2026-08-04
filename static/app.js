/* 單頁互動：呼叫固定 API、更新卡片、Leaflet 與唯一 Chart.js 圖表。 */
const districts = ["松山區","信義區","大安區","中山區","中正區","大同區","萬華區","文山區","南港區","內湖區","士林區","北投區"];
let map = L.map("map").setView([25.0478, 121.5319], 12);
let markerLayer = L.layerGroup().addTo(map);
let historyChart = null;
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution:"© OpenStreetMap contributors" }).addTo(map);

const districtSelect = document.querySelector("#district");
districts.forEach(name => districtSelect.add(new Option(name, name)));
const localNow = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0,16);
document.querySelector("#arrival-time").value = localNow;

async function submitQuery(payload) {
  showStatus("正在分析停車難度…", "");
  const response = await fetch("/api/query", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) {
    if (data.fallback === "manual") document.querySelector("#manual-panel").open = true;
    throw new Error(data.error || "查詢失敗");
  }
  renderSummary(data); renderCards(data); renderMap(data);
  const historyOk = data.recommendations.length ? await loadHistory(data.recommendations[0].lot_id) : true;
  if (historyOk) showStatus("分析完成；數字來自官方資料與固定公式。", "success");
}

document.querySelector("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  try { await submitQuery({ mode:"chat", message:document.querySelector("#message").value }); }
  catch (error) { showStatus(error.message, "error"); }
});

document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault();
  const arrival = new Date(document.querySelector("#arrival-time").value).toISOString();
  try { await submitQuery({ mode:"manual", address:document.querySelector("#address").value,
    district:districtSelect.value, arrival_time:arrival }); }
  catch (error) { showStatus(error.message, "error"); }
});

function showStatus(message, type) { const node=document.querySelector("#status"); node.textContent=message; node.className=type; }
function formatDistance(value) { return value == null ? "行政區模式" : value < 1000 ? `${Math.round(value)} m` : `${(value/1000).toFixed(1)} km`; }
function renderSummary(data) {
  document.querySelector("#destination").textContent = data.destination?.display_address || "行政區查詢";
  document.querySelector("#district-score").textContent = data.current.district_score == null ? "資料不足" : `${data.current.district_score} 分`;
  document.querySelector("#history-score").textContent = data.history.hell_score == null ? "樣本不足" : `${data.history.hell_score} 分`;
  const compare=data.history.comparison; const weekday=compare?.weekday?.hell_score; const weekend=compare?.weekend?.hell_score;
  document.querySelector("#history-compare").textContent = weekday == null || weekend == null ? `有效樣本 ${data.history.sample_count} 筆` : `平日 ${weekday}｜週末 ${weekend}`;
  document.querySelector("#valid-count").textContent = `${data.current.valid_lot_count} 座`;
  const officialTime = data.official_updated_at
    ? new Date(data.official_updated_at).toLocaleString("zh-TW") : "無資料";
  const collectedTime = data.collected_at
    ? new Date(data.collected_at).toLocaleString("zh-TW") : "無資料";
  document.querySelector("#official-updated-at").textContent =
    `官方資料時間：${officialTime}`;
  document.querySelector("#collected-at").textContent =
    `系統最後抓取：${collectedTime}`;
}
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
function renderMap(data) {
  markerLayer.clearLayers();
  const points = [];
  if (data.destination) { L.marker([data.destination.latitude,data.destination.longitude]).bindPopup("目的地").addTo(markerLayer); points.push([data.destination.latitude,data.destination.longitude]); }
  data.recommendations.concat(data.warning,data.avoid).forEach(lot=>{
    if (lot.latitude == null) return;
    L.circleMarker([lot.latitude,lot.longitude],{radius:8,color:lot.hell_score>=95?"#ff4d4f":lot.hell_score>=80?"#f6c344":"#4ecb8d"})
      .bindPopup(`${lot.lot_name}<br>剩餘 ${lot.available_spaces} 格<br>${formatDistance(lot.distance_m)}`).addTo(markerLayer);
    points.push([lot.latitude,lot.longitude]);
  });
  if (points.length) map.fitBounds(points,{padding:[28,28]});
}
async function loadHistory(lotId) {
  const response = await fetch(`/api/parking/${encodeURIComponent(lotId)}/history`); const data = await response.json();
  if (!response.ok) { showStatus(data.error,"error"); return false; }
  const labels=data.points.map(x=>new Date(x.captured_at).toLocaleString("zh-TW")); const values=data.points.map(x=>x.available_spaces);
  if (historyChart) historyChart.destroy();
  historyChart=new Chart(document.querySelector("#history-chart"),{type:"line",data:{labels,datasets:[{label:"剩餘汽車位",data:values,borderColor:"#ff8a3d",backgroundColor:"#ff8a3d33",fill:true,tension:.25}]},options:{responsive:true,scales:{y:{beginAtZero:true}}}});
  document.querySelector("#history-note").textContent = data.points.length ? `共 ${data.points.length} 筆有效歷史資料` : "歷史樣本尚不足";
  return true;
}
