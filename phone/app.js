// RoomCam: app del teléfono (PWA). Sin dependencias externas.

const $app = document.getElementById("app");
const API = location.origin;
const STORE = {
  get: (k) => { try { return localStorage.getItem("roomcam." + k); } catch (_) { return null; } },
  set: (k, v) => { try { localStorage.setItem("roomcam." + k, v); } catch (_) {} },
  del: (k) => { try { localStorage.removeItem("roomcam." + k); } catch (_) {} },
};

const state = {
  demo: STORE.get("demo") === "1",
  access: null,
  accessExp: 0,
  ws: null,
  wsReady: false,
  laptops: {},          // id -> {name, online, monitoring, state, camera, updated_at}
  devices: [],
  events: [],
  me: null,
  config: null,
  live: null,           // {pc, laptopId, timer}
  listeners: new Set(),
};

const isStandalone = () => window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (ts) => new Date(ts * 1000).toLocaleString("es", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const ago = (ts) => {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} días`;
};

// ------------------------------------------------------------------ API

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

async function request(method, path, body, auth = true) {
  const headers = { "Content-Type": "application/json" };
  if (auth) headers.Authorization = "Bearer " + (await accessToken());
  let r;
  try {
    r = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (_) {
    throw new ApiError("No hay conexión con el servidor. Revisa tu internet.", 0);
  }
  const data = r.status === 204 ? {} : await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401 && auth) await lostAccess(data.detail);
    throw new ApiError(typeof data.detail === "string" ? data.detail : `Error del servidor (${r.status}).`, r.status);
  }
  return data;
}

function saveTokens(t) {
  state.access = t.access_token;
  state.accessExp = Date.now() + (t.expires_in - 30) * 1000;
  state.me = t.device_id;
  STORE.set("refresh", t.refresh_token);
  STORE.set("device_id", t.device_id);
}

async function accessToken() {
  if (state.access && Date.now() < state.accessExp) return state.access;
  const refresh = STORE.get("refresh");
  if (!refresh) throw new ApiError("Este teléfono no está emparejado.", 401);
  const t = await request("POST", "/api/auth/refresh", { refresh_token: refresh }, false).catch(async (e) => {
    if (e.status === 401) await lostAccess("Este teléfono fue revocado o su sesión caducó.");
    throw e;
  });
  saveTokens(t);
  return state.access;
}

async function lostAccess(reason) {
  STORE.del("refresh");
  STORE.del("device_id");
  state.access = null;
  closeWs();
  stopLive();
  go("#/pair", reason || "Este teléfono ya no tiene acceso. Vuelve a emparejarlo.");
}

// ------------------------------------------------------------------ tiempo real

let wsRetry = 1;
function openWs() {
  if (state.demo || state.ws) return;
  const ws = new WebSocket(API.replace(/^http/, "ws") + "/ws");
  state.ws = ws;
  ws.onopen = async () => {
    try { ws.send(JSON.stringify({ type: "auth", token: await accessToken() })); } catch (_) { ws.close(); }
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "auth_ok") { state.wsReady = true; wsRetry = 1; setOffline(false); }
    else if (msg.type === "auth_error") { lostAccess(msg.detail); }
    else if (msg.type === "closed") { lostAccess(msg.reason === "revoked" ? "Este teléfono fue revocado." : "La cuenta fue eliminada."); }
    else if (msg.type === "presence") {
      state.laptops[msg.laptop_id] = { ...(state.laptops[msg.laptop_id] || {}), ...msg };
      if (state.live && state.live.laptopId === msg.laptop_id && !msg.monitoring) {
        liveMessage(msg.online ? "La vigilancia se detuvo en la laptop. La vista en vivo se cerró." : "La laptop se desconectó.");
        stopLive(false);
      }
    }
    state.listeners.forEach((fn) => fn(msg));
    render();
  };
  ws.onclose = () => {
    state.ws = null; state.wsReady = false;
    if (!STORE.get("refresh")) return;
    setOffline(true);
    setTimeout(openWs, wsRetry * 1000);
    wsRetry = Math.min(wsRetry * 2, 30);
  };
}
function closeWs() { if (state.ws) { state.ws.onclose = null; state.ws.close(); } state.ws = null; state.wsReady = false; }
function wsSend(msg) { if (state.ws && state.wsReady) state.ws.send(JSON.stringify(msg)); else throw new Error("offline"); }
function setOffline(v) { document.getElementById("offline-banner").hidden = !v || state.demo; }

// ------------------------------------------------------------------ notificaciones

function b64ToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function enableAlerts() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    throw new Error(isIOS() && !isStandalone()
      ? "En iPhone primero añade RoomCam a la pantalla de inicio y ábrela desde ahí."
      : "Este navegador no admite notificaciones push. En iPhone hace falta iOS 16.4 o superior.");
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Permiso de notificaciones denegado. Actívalo en Ajustes > Notificaciones > RoomCam.");
  if (state.demo) return;
  const cfg = state.config || await request("GET", "/api/config", null, false);
  if (!cfg.vapid_public_key) throw new Error("El servidor todavía no tiene configuradas las notificaciones (claves VAPID).");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.vapid_public_key) });
  const json = sub.toJSON();
  await request("PUT", "/api/devices/me/push", { endpoint: json.endpoint, keys: json.keys });
  await request("PATCH", "/api/devices/me", { alerts_enabled: true });
}

async function disableAlerts() {
  if (state.demo) return;
  await request("PATCH", "/api/devices/me", { alerts_enabled: false });
}

// ------------------------------------------------------------------ vista en vivo

function liveMessage(text) {
  const o = document.getElementById("live-overlay");
  if (o) { o.textContent = text; o.hidden = !text; }
}

async function startLive(laptopId) {
  try { await startLiveInner(laptopId); }
  catch (e) { liveMessage("No se pudo abrir la vista en vivo: " + (e.message || e)); stopLive(false); }
}

async function startLiveInner(laptopId) {
  stopLive(false);
  liveMessage("Conectando con la laptop…");
  if (state.demo) return startDemoLive();
  const laptop = state.laptops[laptopId];
  if (!laptop?.online) return liveMessage("La laptop está desconectada o dormida. No hay vídeo disponible.");
  if (!laptop.monitoring) return liveMessage("La vigilancia está detenida. Iníciala en la laptop para ver en vivo.");

  let ice = [];
  try { ice = (await request("GET", "/api/ice")).ice_servers; } catch (e) { return liveMessage(e.message); }
  const pc = new RTCPeerConnection({ iceServers: ice });
  pc.addTransceiver("video", { direction: "recvonly" });
  const live = { pc, laptopId, timer: null, listener: null };
  state.live = live;

  pc.ontrack = (ev) => {
    live.stream = ev.streams[0] || new MediaStream([ev.track]);
    attachStream(live.stream);
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === "connected") { liveMessage(""); clearTimeout(live.timer); }
    if (pc.connectionState === "failed") {
      liveMessage(state.config?.turn_enabled
        ? "No se pudo establecer la conexión de vídeo. Inténtalo de nuevo."
        : "No se pudo conectar el vídeo. Tu red probablemente necesita un servidor TURN (ver guía de despliegue).");
      stopLive(false);
    }
  };
  live.listener = async (msg) => {
    if (msg.from !== laptopId) return;
    if (msg.type === "answer") await pc.setRemoteDescription({ type: "answer", sdp: msg.sdp });
    if (msg.type === "live_error") { liveMessage(msg.detail); stopLive(false); }
    if (msg.type === "hangup") { liveMessage("La vista en vivo se cerró: la vigilancia se detuvo en la laptop."); stopLive(false); }
  };
  state.listeners.add(live.listener);

  await pc.setLocalDescription(await pc.createOffer());
  // Esperar a reunir candidatos ICE (como máximo 3 s) para enviar una sola oferta.
  await new Promise((res) => {
    if (pc.iceGatheringState === "complete") return res();
    const t = setTimeout(res, 3000);
    pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") { clearTimeout(t); res(); } };
  });
  try { wsSend({ type: "offer", to: laptopId, sdp: pc.localDescription.sdp }); }
  catch (_) { liveMessage("Sin conexión con el servidor."); return stopLive(false); }
  live.timer = setTimeout(() => {
    if (pc.connectionState !== "connected") { liveMessage("La laptop no respondió. La transmisión no está disponible."); stopLive(false); }
  }, 20000);
}

function stopLive(notify = true) {
  const live = state.live;
  if (!live) return;
  state.live = null;
  clearTimeout(live.timer);
  if (live.listener) state.listeners.delete(live.listener);
  if (live.demoAnim) cancelAnimationFrame(live.demoAnim);
  if (live.pc) {
    if (notify) { try { wsSend({ type: "hangup", to: live.laptopId }); } catch (_) {} }
    live.pc.close();
  }
  const v = document.getElementById("live-video");
  if (v) v.srcObject = null;
  const tag = document.querySelector(".video-wrap .tag");
  if (tag) tag.hidden = true;
}

function startDemoLive() {
  const canvas = document.getElementById("live-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  canvas.width = 640; canvas.height = 360;
  const live = { laptopId: "demo", demoAnim: 0 };
  state.live = live;
  liveMessage("");
  const t0 = performance.now();
  const draw = (t) => {
    const s = (t - t0) / 1000;
    ctx.fillStyle = "#2b2f36"; ctx.fillRect(0, 0, 640, 360);
    ctx.fillStyle = "#3c4048"; ctx.fillRect(40, 220, 560, 120);
    const x = 320 + Math.sin(s) * 200;
    ctx.fillStyle = "#9aa0a6"; ctx.beginPath(); ctx.arc(x, 150, 28, 0, 7); ctx.fill(); ctx.fillRect(x - 25, 180, 50, 90);
    ctx.fillStyle = "#fdd663"; ctx.font = "bold 28px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("VÍDEO SIMULADO", 320, 60);
    ctx.font = "16px sans-serif"; ctx.fillStyle = "#fff";
    ctx.fillText(new Date().toLocaleTimeString("es"), 320, 90);
    live.demoAnim = requestAnimationFrame(draw);
  };
  live.demoAnim = requestAnimationFrame(draw);
}

// ------------------------------------------------------------------ datos

async function loadAll() {
  if (state.demo) {
    state.devices = [
      { id: "demo_laptop", kind: "laptop", name: "Laptop HP (SIMULADA)" },
      { id: "demo_me", kind: "phone", name: "Este iPhone (SIMULADO)", is_me: true, alerts_enabled: STORE.get("demo_alerts") !== "0" },
    ];
    state.laptops = { demo_laptop: { name: "Laptop HP (SIMULADA)", online: true, monitoring: STORE.get("demo_monitoring") !== "0", camera: "Cámara simulada", updated_at: Date.now() / 1000 } };
    state.events = JSON.parse(STORE.get("demo_events") || "[]");
    return;
  }
  const [cfg, devs, evs] = await Promise.all([
    request("GET", "/api/config", null, false), request("GET", "/api/devices"), request("GET", "/api/events"),
  ]);
  state.config = cfg;
  state.devices = devs.devices;
  state.events = evs.events;
  for (const d of devs.devices) {
    if (d.kind === "laptop") state.laptops[d.id] = { ...(state.laptops[d.id] || {}), name: d.name, ...(d.status || {}) };
  }
}

function laptopChip(l) {
  if (!l || !l.online) {
    return l?.state === "asleep" ? ["Dormida", "amber"] : ["Desconectada", "grey"];
  }
  return l.monitoring ? ["Vigilando", "red"] : ["Vigilancia detenida", "grey"];
}

// ------------------------------------------------------------------ pantallas

let flash = "";
function go(hash, message = "") { flash = message; if (location.hash === hash) render(); else location.hash = hash; }

function route() {
  const [path, query] = (location.hash || "#/").slice(1).split("?");
  return { path, params: new URLSearchParams(query || "") };
}

function render() {
  const { path, params } = route();
  if (path !== "/live") delete $app.dataset.view;
  document.getElementById("demo-banner").hidden = !state.demo;
  const paired = state.demo || !!STORE.get("refresh");
  if (path === "/pair" || !paired) return renderPair(params);
  if (path === "/live") return renderLive(params.get("laptop"));
  return renderHome();
}

function renderPair(params) {
  stopLive(false);
  const code = params.get("code") || "";
  const needInstall = isIOS() && !isStandalone();
  $app.innerHTML = `
    <h1>Emparejar con tu laptop</h1>
    ${flash ? `<div class="card error">${esc(flash)}</div>` : ""}
    ${needInstall ? `
      <div class="card">
        <h2>Primero instala la app</h2>
        <p>En iPhone, las notificaciones solo funcionan si RoomCam está en la pantalla de inicio.</p>
        <ol>
          <li>Toca el botón <b>Compartir</b> de Safari (el cuadrado con la flecha).</li>
          <li>Elige <b>Añadir a pantalla de inicio</b>.</li>
          <li>Abre RoomCam desde el icono nuevo y escribe ahí el código.</li>
        </ol>
        ${code ? `<p>Tu código: <b style="font-size:20px;letter-spacing:2px">${esc(code)}</b></p>` : ""}
        <p class="muted">Puedes emparejar desde Safari igualmente, pero la app instalada guarda su sesión por separado.</p>
      </div>` : ""}
    <div class="card">
      <h2>Código de emparejamiento</h2>
      <p class="muted">En la laptop abre RoomCam > Teléfonos > «Emparejar un teléfono». El código caduca en 5 minutos y solo sirve una vez.</p>
      <input id="code" autocomplete="one-time-code" maxlength="9" placeholder="ABCD-EFGH" value="${esc(code)}">
      <button class="btn" id="pair">Emparejar</button>
      <div id="pair-error" class="error"></div>
    </div>
    <div class="card">
      <h2>¿Aún no tienes la laptop lista?</h2>
      <p class="muted">Prueba la app con una laptop, alertas y vídeo simulados. Nada de lo que veas en el modo demo es real.</p>
      <button class="btn secondary" id="demo">Probar modo demo</button>
    </div>`;
  flash = "";
  document.getElementById("pair").onclick = async () => {
    const err = document.getElementById("pair-error");
    err.textContent = "";
    const value = document.getElementById("code").value.trim();
    if (value.replace(/[^a-z0-9]/gi, "").length !== 8) { err.textContent = "El código tiene 8 caracteres."; return; }
    try {
      const name = isIOS() ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android" : "Teléfono";
      const t = await request("POST", "/api/pairing/claim", { code: value, device_name: name }, false);
      state.demo = false; STORE.del("demo");
      saveTokens(t);
      await loadAll().catch(() => {});
      openWs();
      go("#/home", "Emparejado. Activa las alertas para recibir avisos.");
    } catch (e) { err.textContent = e.message; }
  };
  document.getElementById("demo").onclick = () => { state.demo = true; STORE.set("demo", "1"); go("#/home"); boot(); };
}

async function renderHome() {
  const laptops = Object.entries(state.laptops);
  const me = state.devices.find((d) => d.is_me);
  const alertsOn = me ? !!me.alerts_enabled && (state.demo || me.has_push) : false;
  $app.innerHTML = `
    <h1>RoomCam</h1>
    ${flash ? `<div class="card">${esc(flash)}</div>` : ""}
    ${laptops.length ? laptops.map(([id, l]) => {
      const [label, color] = laptopChip(l);
      return `<div class="card">
        <div class="row"><h2>${esc(l.name || "Laptop")}</h2><span class="chip ${color}">${label}</span></div>
        <p class="muted">${l.online ? (l.monitoring ? `Cámara en uso: ${esc(l.camera || "sí")}` : "Conectada, cámara apagada.") :
          `Sin conexión ${l.updated_at ? ago(l.updated_at) : ""}. Puede estar apagada, dormida o sin internet.`}</p>
        <button class="btn" data-live="${esc(id)}" ${l.online && l.monitoring ? "" : "disabled"}>Ver en vivo</button>
        ${l.online && !l.monitoring ? `<p class="muted">Para ver en vivo, inicia la vigilancia en la laptop.</p>` : ""}
      </div>`;
    }).join("") : `<div class="card"><p>No hay ninguna laptop en esta cuenta.</p></div>`}
    <div class="card">
      <div class="row"><div><h2>Alertas de movimiento</h2><p class="muted">Recibe una notificación con la hora del evento. Sin imágenes.</p></div>
        <label class="switch"><input type="checkbox" id="alerts" ${alertsOn ? "checked" : ""}><span></span></label></div>
      <div id="alerts-error" class="error"></div>
      ${state.demo ? `<button class="btn secondary" id="demo-alert">Simular una alerta</button>` : ""}
    </div>
    <div class="card">
      <div class="row"><h2>Historial</h2>${state.events.length ? `<button class="link" id="clear-events">Borrar todo</button>` : ""}</div>
      ${state.events.length ? `<ul class="list">${state.events.slice(0, 20).map((e) =>
        `<li><span>${fmt(e.ts)}</span><span class="muted">${esc(e.laptop_name || "")}</span></li>`).join("")}</ul>`
        : `<p class="muted">Sin alertas. ${state.demo ? "" : `Se guardan ${state.config?.event_retention_days ?? 30} días.`}</p>`}
    </div>
    <div class="card">
      <h2>Dispositivos emparejados</h2>
      <ul class="list">${state.devices.map((d) => `<li>
        <span>${d.kind === "laptop" ? "💻" : "📱"} ${esc(d.name)}${d.is_me ? " (este)" : ""}</span>
        <button class="link" data-revoke="${esc(d.id)}" data-me="${d.is_me ? 1 : 0}">${d.is_me ? "Desemparejar" : "Revocar"}</button>
      </li>`).join("")}</ul>
      <p class="muted">Revocar quita el acceso al instante. Para borrar la cuenta entera, usa la app de la laptop.</p>
    </div>`;
  flash = "";

  $app.querySelectorAll("[data-live]").forEach((b) => b.onclick = () => go(`#/live?laptop=${b.dataset.live}`));
  document.getElementById("alerts").onchange = async (ev) => {
    const err = document.getElementById("alerts-error");
    err.textContent = "";
    try {
      if (ev.target.checked) await enableAlerts(); else await disableAlerts();
      if (state.demo) STORE.set("demo_alerts", ev.target.checked ? "1" : "0");
      await loadAll(); render();
    } catch (e) { ev.target.checked = !ev.target.checked; err.textContent = e.message; }
  };
  const demoBtn = document.getElementById("demo-alert");
  if (demoBtn) demoBtn.onclick = demoAlert;
  const clear = document.getElementById("clear-events");
  if (clear) clear.onclick = async () => {
    if (!confirm("¿Borrar todo el historial de alertas?")) return;
    if (state.demo) STORE.del("demo_events"); else await request("DELETE", "/api/events").catch((e) => alert(e.message));
    await loadAll(); render();
  };
  $app.querySelectorAll("[data-revoke]").forEach((b) => b.onclick = async () => {
    const me = b.dataset.me === "1";
    if (!confirm(me ? "¿Desemparejar este teléfono? Dejarás de recibir alertas." : "¿Quitar el acceso a este dispositivo?")) return;
    if (state.demo) { if (me) return exitDemo(); alert("En el modo demo no se puede revocar la laptop simulada."); return; }
    try {
      await request("DELETE", `/api/devices/${b.dataset.revoke}`);
      if (me) return lostAccess("Teléfono desemparejado.");
      await loadAll(); render();
    } catch (e) { alert(e.message); }
  });
}

async function demoAlert() {
  const ts = Date.now() / 1000;
  const evs = JSON.parse(STORE.get("demo_events") || "[]");
  evs.unshift({ ts, laptop_name: "Laptop HP (SIMULADA)" });
  STORE.set("demo_events", JSON.stringify(evs.slice(0, 50)));
  let shown = false;
  if ("Notification" in window && Notification.permission === "granted" && "serviceWorker" in navigator) {
    const reg = await navigator.serviceWorker.ready;
    await reg.showNotification("[SIMULADO] Movimiento detectado", {
      body: `${fmt(ts)} · notificación de prueba, no viene de ninguna laptop`,
      tag: "roomcam-demo", data: { url: "./#/live?laptop=demo_laptop" },
    });
    shown = true;
  }
  await loadAll();
  go("#/home", shown ? "Se mostró una notificación SIMULADA." : "Alerta SIMULADA añadida al historial. Activa las alertas para verla como notificación.");
}

function renderLive(laptopId) {
  const l = state.laptops[laptopId];
  const [label, color] = laptopChip(l);
  // Si la pantalla ya está construida, solo actualizar el estado (no reiniciar el vídeo).
  if ($app.dataset.view === "live:" + laptopId) {
    const chip = document.getElementById("live-chip");
    if (chip) { chip.textContent = label; chip.className = "chip " + color; }
    return;
  }
  $app.dataset.view = "live:" + laptopId;
  $app.innerHTML = `
    <div class="row"><button class="link" id="back">‹ Volver</button><span id="live-chip" class="chip ${color}">${label}</span></div>
    <h1>${esc(l?.name || "Vista en vivo")}</h1>
    <div class="video-wrap">
      ${state.demo ? `<canvas id="live-canvas"></canvas>` : `<video id="live-video" playsinline autoplay muted></video>`}
      <span class="tag chip ${state.demo ? "amber" : "red"}">${state.demo ? "SIMULADO" : "EN VIVO"}</span>
      <div class="overlay" id="live-overlay" hidden></div>
    </div>
    <p class="muted" style="margin-top:12px">${state.demo ? "Vídeo de prueba generado en el teléfono." :
      "Vídeo cifrado de extremo a extremo (DTLS-SRTP). No se graba. La laptop muestra que lo estás viendo."}</p>
    <button class="btn" id="retry">Reconectar</button>
    <button class="btn secondary" id="stop">Cerrar vista en vivo</button>`;
  document.getElementById("back").onclick = () => { stopLive(); go("#/home"); };
  document.getElementById("stop").onclick = () => { stopLive(); go("#/home"); };
  document.getElementById("retry").onclick = () => startLive(laptopId);
  if (!state.live || state.live.laptopId !== laptopId) startLive(laptopId);
  else if (state.live.stream) attachStream(state.live.stream);
}

function attachStream(stream) {
  const v = document.getElementById("live-video");
  if (v && v.srcObject !== stream) { v.srcObject = stream; v.play().catch(() => {}); }
}

function exitDemo() {
  stopLive(false);
  state.demo = false;
  ["demo", "demo_events", "demo_alerts", "demo_monitoring"].forEach(STORE.del);
  state.laptops = {}; state.devices = []; state.events = [];
  go("#/pair");
}

// ------------------------------------------------------------------ arranque

let lastPath = "";
window.addEventListener("hashchange", () => {
  const { path } = route();
  if (lastPath === "/live" && path !== "/live") stopLive();
  lastPath = path;
  render();
});
document.getElementById("exit-demo").onclick = exitDemo;
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.type === "open") location.href = e.data.url;
});
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && (state.demo || STORE.get("refresh"))) {
    try { await loadAll(); openWs(); render(); } catch (_) {}
  }
});

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  state.me = STORE.get("device_id");
  lastPath = route().path;
  if (state.demo || STORE.get("refresh")) {
    try { await loadAll(); } catch (e) { if (e.status !== 401) flash = e.message; }
    openWs();
  }
  render();
}

boot();
