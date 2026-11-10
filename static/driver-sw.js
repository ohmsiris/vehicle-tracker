/* สายรถ · ใบขาย -- keeps the driver's page opening without signal. The page and its two files are served from the phone and refreshed
   in the background (a new version shows on the next open); the API always goes to the network (the page keeps its own copy of the
   day and sends it when the signal is back). Bump VERSION when driver.js / driver.css change shape. */
const VERSION = "icebox-d-v3";
const SHELL = ["/d/", "/static/driver.css", "/static/driver.js", "/static/driver.webmanifest", "/static/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  const key = url.pathname === "/d" || url.pathname === "/d/" ? "/d/" : url.pathname;
  if (SHELL.indexOf(key) < 0) return;
  e.respondWith(caches.open(VERSION).then((cache) => cache.match(key).then((hit) => {
    const fresh = fetch(e.request).then((resp) => { if (resp && resp.ok) cache.put(key, resp.clone()); return resp; }).catch(() => hit);
    return hit || fresh;                       // from the phone at once; the network copy is stored for next time
  })));
});
