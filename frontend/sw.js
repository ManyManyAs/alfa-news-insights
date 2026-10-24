// Service worker Бизнес-радара: push-уведомления (новый риск, дайджест недели) и работа без сети —
// оболочка приложения и последняя загруженная лента открываются офлайн (этап 9). При выходе кэш данных удаляется.
const SHELL = "br-shell-v1", API = "br-api";
// данные, которые имеет смысл показать без сети: кто я, профиль компании, лента, прогнозы банка
// личные данные кэшируются только для работы без сети; при выходе и смене пользователя кэш удаляется (ui.js, app.js)
const API_OFFLINE = [/^\/api\/me$/, /^\/api\/companies\/[^/]+$/, /^\/api\/feed$/, /^\/api\/bank\/forecasts$/, /^\/api\/demo-users$/];

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { title: "Бизнес-радар", body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(d.title || "Бизнес-радар", {
    body: d.body || "", tag: d.tag || "radar", renotify: true, data: { url: d.url || "/" },
    icon: "/static/icon-192.png", badge: "/static/icon-192.png", lang: "ru",
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if (c.url.startsWith(self.location.origin)) { c.navigate(url); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k.startsWith("br-shell-") && k !== SHELL) await caches.delete(k);
  await self.clients.claim();
})()));

// сеть — первой; без сети — последняя сохранённая копия. Статика с ?v= неизменна — из кэша сразу.
async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw e;
  }
}
async function cacheFirst(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/api/auth/logout") return;
  if (url.pathname.startsWith("/static/") && url.searchParams.get("v")) return event.respondWith(cacheFirst(req));
  if (url.pathname === "/" || url.pathname === "/index.html") return event.respondWith(networkFirst(req, SHELL));
  if (API_OFFLINE.some((r) => r.test(url.pathname))) return event.respondWith(networkFirst(req, API));
});
