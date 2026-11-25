// Service worker: recibe las notificaciones push y abre la app al tocarlas.
// La notificación solo trae la hora y un enlace; nunca imágenes.

const CACHE = "roomcam-v1";
const SHELL = ["./", "index.html", "app.js", "styles.css", "manifest.webmanifest", "icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Nunca cachear la API ni la señalización.
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws") || e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});

function timeText(ts) {
  return new Date(ts * 1000).toLocaleString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" });
}

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}
  const demo = data.type === "demo";
  const title = demo ? "[SIMULADO] Movimiento detectado" : "Movimiento detectado";
  const body = `${timeText(data.ts || Date.now() / 1000)} · ${data.laptop_name || "Tu laptop"}` + (demo ? " · notificación de prueba" : "");
  e.waitUntil(self.registration.showNotification(title, {
    body,
    tag: "roomcam-motion",
    renotify: true,
    data: { url: data.url || "./" },
    actions: [{ action: "live", title: "Ver en vivo" }],
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "./", self.registration.scope).href;
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      if ("focus" in c) { await c.focus(); c.postMessage({ type: "open", url: target }); return; }
    }
    await self.clients.openWindow(target);
  })());
});
