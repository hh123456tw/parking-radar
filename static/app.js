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
  document.querySelector("#updated-at").textContent = data.updated_at ? `資料時間 ${new Date(data.updated_at).toLocaleString("zh-TW")}` : "目前無有效即時資料";
}
function parkingCard(lot, kind="") {
  return `<article class="parking-card ${kind}"><p>${lot.hell_label}</p><h3>${lot.lot_name}</h3><strong>剩餘 ${lot.available_spaces} 格</strong><p>地獄指數 ${lot.hell_score}｜${formatDistance(lot.distance_m)}</p><p>推薦分數 ${lot.recommendation_score}</p></article>`;
}
function renderCards(data) {
  const cards = [...data.recommendations.map(x=>parkingCard(x)), ...data.warning.map(x=>parkingCard(x,"warning")), ...data.avoid.map(x=>parkingCard(x,"avoid"))];
  document.querySelector("#recommendations").innerHTML = cards.join("") || "<p>目前沒有符合條件的停車場。</p>";
  document.querySelector("#nearest").innerHTML = data.nearest.map(x=>`<li><button type="button" data-lot="${x.lot_id}">${x.lot_name}<br>${formatDistance(x.distance_m)}</button></li>`).join("");
  document.querySelectorAll("[data-lot]").forEach(button=>button.addEventListener("click",()=>loadHistory(button.dataset.lot).catch(err => showStatus(err.message, "error"))));
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
