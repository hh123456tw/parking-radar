/* 服務器只快取應用程式外殼；API、地圖圖磚與外部連結一律直通網路。 */
/* 版本由 Flask 依前端檔案內容雜湊帶入，檔案一改就換新快取，不需手動升版。 */
const ASSET_VERSION = "{{ asset_version }}";
const CACHE_NAME = `parking-radar-shell-${ASSET_VERSION}`;
const SHELL_ASSETS = [
  "/",
  `/static/style.css?v=${ASSET_VERSION}`,
  `/static/app.js?v=${ASSET_VERSION}`,
  `/static/vendor/leaflet/leaflet.css?v=${ASSET_VERSION}`,
  `/static/vendor/leaflet/leaflet.js?v=${ASSET_VERSION}`,
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
];

// 新版安裝完成後立即接手，避免使用者要關掉所有分頁才吃到新版。
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME)
    .then(cache => cache.addAll(SHELL_ASSETS))
    .then(() => self.skipWaiting()));
});

// 換版時清掉舊 cache，避免殘留已過期的殼層。
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(names => Promise.all(
    names.filter(name => name !== CACHE_NAME).map(name => caches.delete(name)),
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  // 資料、非 GET 與外部請求在快取找查前直接放行，避免擋到即時資料。
  if (request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) return;
  if (url.pathname.startsWith("/admin/")) return;
  if (url.origin !== self.location.origin) return;
  if (url.hostname.endsWith("tile.openstreetmap.org")) return;
  if (url.href.includes("google.com/maps")) return;

  // 導航優先走網路；只有首頁回應才寫入外殼快取，避免其他頁面覆蓋離線首頁。
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).then(response => {
      if (response.ok && url.pathname === "/") {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put("/", copy));
      }
      return response;
    }).catch(() => caches.match("/")));
    return;
  }

  // 同源靜態資源快取優先，未命中才抓取並寫入快取。
  event.respondWith(caches.match(request).then(cached => {
    if (cached) return cached;
    return fetch(request).then(response => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    });
  }));
});
