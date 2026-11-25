"""Servidor RoomCam: cuentas, emparejamiento, señalización WebRTC y notificaciones.

El servidor nunca recibe vídeo ni imágenes. Solo reenvía mensajes de señalización
entre dispositivos de la misma cuenta y guarda la hora de los eventos de movimiento.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import security
from .config import Settings
from .db import Database
from .push import PushSender

log = logging.getLogger("roomcam")

# ---------------------------------------------------------------- modelos


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)


class LoginIn(BaseModel):
    email: str
    password: str
    device_name: str = Field(default="Laptop", max_length=64)


class RefreshIn(BaseModel):
    refresh_token: str


class ClaimIn(BaseModel):
    code: str = Field(max_length=32)
    device_name: str = Field(default="Teléfono", max_length=64)


class PrefsIn(BaseModel):
    alerts_enabled: bool


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: dict[str, str]


class MotionIn(BaseModel):
    ts: float | None = None


class DeleteAccountIn(BaseModel):
    password: str


class Principal(BaseModel):
    device_id: str
    user_id: int
    kind: Literal["laptop", "phone"]


# ---------------------------------------------------------------- conexiones en tiempo real


class Hub:
    """Conexiones WebSocket activas y estado de presencia de las laptops."""

    def __init__(self) -> None:
        self.sockets: dict[str, WebSocket] = {}
        self.owner: dict[str, int] = {}
        self.presence: dict[str, dict] = {}

    def user_devices(self, user_id: int) -> list[str]:
        return [d for d, u in self.owner.items() if u == user_id]

    async def send(self, device_id: str, message: dict) -> bool:
        ws = self.sockets.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message))
            return True
        except Exception:
            return False

    async def broadcast_presence(self, user_id: int, laptop_id: str) -> None:
        msg = {"type": "presence", "laptop_id": laptop_id, **self.laptop_state(laptop_id)}
        for dev in self.user_devices(user_id):
            if dev != laptop_id:
                await self.send(dev, msg)

    def laptop_state(self, laptop_id: str) -> dict:
        online = laptop_id in self.sockets
        p = self.presence.get(laptop_id, {})
        return {
            "online": online,
            "monitoring": bool(online and p.get("monitoring")),
            "state": p.get("state", "online" if online else "offline") if online else p.get("last_state", "offline"),
            "camera": p.get("camera") if online else None,
            "updated_at": p.get("updated_at"),
        }

    async def disconnect(self, device_id: str, reason: str = "revoked") -> None:
        ws = self.sockets.pop(device_id, None)
        self.owner.pop(device_id, None)
        if ws is not None:
            try:
                await ws.send_text(json.dumps({"type": "closed", "reason": reason}))
                await ws.close(code=4001)
            except Exception:
                pass


# ---------------------------------------------------------------- aplicación


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_path)
    hub = Hub()
    pusher = PushSender(settings)
    login_failures: dict[str, list[float]] = defaultdict(list)
    last_push: dict[str, float] = {}

    app = FastAPI(title="RoomCam", version="0.1.0")
    app.state.settings = settings
    app.state.db = db
    app.state.hub = hub
    app.state.pusher = pusher

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_url],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.dev_secret:
        log.warning("ROOMCAM_JWT_SECRET no definido: usando un secreto temporal (solo desarrollo)")

    # ------------------------------------------------ utilidades

    def issue_tokens(device_id: str, user_id: int, kind: str) -> dict:
        refresh = security.new_refresh_token()
        db.execute(
            "UPDATE devices SET refresh_hash = ?, refresh_expires = ?, last_seen = ? WHERE id = ?",
            (security.sha256(refresh), time.time() + settings.refresh_token_days * 86400, time.time(), device_id),
        )
        access = security.make_access_token(
            settings.jwt_secret, device_id=device_id, user_id=user_id, kind=kind,
            minutes=settings.access_token_minutes,
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": settings.access_token_minutes * 60,
            "device_id": device_id,
            "kind": kind,
        }

    def authenticate(token: str) -> Principal:
        try:
            claims = security.decode_access_token(settings.jwt_secret, token)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La sesión caducó. Renueva el token.")
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token no válido.")
        # Comprobación en la base de datos: un dispositivo revocado deja de funcionar al instante.
        row = db.one("SELECT user_id, kind FROM devices WHERE id = ?", (claims["sub"],))
        if row is None or row["user_id"] != claims["uid"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Este dispositivo fue revocado.")
        return Principal(device_id=claims["sub"], user_id=claims["uid"], kind=row["kind"])

    def current(authorization: str = Header(default="")) -> Principal:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el token de acceso.")
        return authenticate(authorization[7:])

    def laptop_only(p: Principal = Depends(current)) -> Principal:
        if p.kind != "laptop":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo una laptop puede hacer esto.")
        return p

    def phone_only(p: Principal = Depends(current)) -> Principal:
        if p.kind != "phone":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo un teléfono puede hacer esto.")
        return p

    def check_rate(key: str) -> None:
        now = time.time()
        recent = [t for t in login_failures[key] if now - t < 900]
        login_failures[key] = recent
        if len(recent) >= 5:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos. Espera 15 minutos.")

    # ------------------------------------------------ información pública

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    def public_config() -> dict:
        return {
            "vapid_public_key": settings.vapid_public_key or None,
            "push_enabled": settings.push_enabled,
            "turn_enabled": settings.turn_enabled,
            "access_token_minutes": settings.access_token_minutes,
            "pairing_code_minutes": settings.pairing_code_minutes,
            "event_retention_days": settings.event_retention_days,
        }

    # ------------------------------------------------ cuentas

    @app.post("/api/auth/register", status_code=201)
    def register(body: RegisterIn) -> dict:
        email = body.email.strip().lower()
        if "@" not in email:
            raise HTTPException(400, "Correo no válido.")
        if db.one("SELECT 1 FROM users WHERE email = ?", (email,)):
            raise HTTPException(409, "Ya existe una cuenta con ese correo.")
        db.execute(
            "INSERT INTO users (email, pw_hash, created_at) VALUES (?, ?, ?)",
            (email, security.hash_password(body.password), time.time()),
        )
        return {"ok": True}

    @app.post("/api/auth/login")
    def login(body: LoginIn) -> dict:
        """Inicia sesión desde la laptop y la registra como dispositivo."""
        email = body.email.strip().lower()
        check_rate(email)
        user = db.one("SELECT id, pw_hash FROM users WHERE email = ?", (email,))
        if user is None or not security.verify_password(user["pw_hash"], body.password):
            login_failures[email].append(time.time())
            raise HTTPException(401, "Correo o contraseña incorrectos.")
        login_failures.pop(email, None)
        device_id = security.new_device_id()
        db.execute(
            "INSERT INTO devices (id, user_id, kind, name, created_at) VALUES (?, ?, 'laptop', ?, ?)",
            (device_id, user["id"], body.device_name.strip() or "Laptop", time.time()),
        )
        return issue_tokens(device_id, user["id"], "laptop")

    @app.post("/api/auth/refresh")
    def refresh(body: RefreshIn) -> dict:
        row = db.one(
            "SELECT id, user_id, kind, refresh_expires FROM devices WHERE refresh_hash = ?",
            (security.sha256(body.refresh_token),),
        )
        if row is None or (row["refresh_expires"] or 0) < time.time():
            raise HTTPException(401, "La sesión del dispositivo ya no es válida. Vuelve a emparejarlo.")
        # Rotación: el token de renovación anterior deja de servir.
        return issue_tokens(row["id"], row["user_id"], row["kind"])

    @app.delete("/api/account")
    async def delete_account(body: DeleteAccountIn, p: Principal = Depends(laptop_only)) -> dict:
        """Borra la cuenta, todos los dispositivos y todos los eventos. No se puede deshacer."""
        user = db.one("SELECT pw_hash FROM users WHERE id = ?", (p.user_id,))
        if user is None or not security.verify_password(user["pw_hash"], body.password):
            raise HTTPException(401, "Contraseña incorrecta.")
        for dev in hub.user_devices(p.user_id):
            await hub.disconnect(dev, "account_deleted")
        db.execute("DELETE FROM users WHERE id = ?", (p.user_id,))
        return {"ok": True}

    # ------------------------------------------------ emparejamiento

    @app.post("/api/pairing/codes")
    def create_pairing_code(p: Principal = Depends(laptop_only)) -> dict:
        code = security.new_pairing_code()
        expires = time.time() + settings.pairing_code_minutes * 60
        db.execute(
            "INSERT INTO pairing_codes (code_hash, user_id, laptop_id, expires_at) VALUES (?, ?, ?, ?)",
            (security.sha256(code), p.user_id, p.device_id, expires),
        )
        pretty = f"{code[:4]}-{code[4:]}"
        return {
            "code": pretty,
            "expires_at": expires,
            "pair_url": f"{settings.public_url}/app/#/pair?code={pretty}",
        }

    @app.post("/api/pairing/claim")
    def claim_pairing_code(body: ClaimIn) -> dict:
        code = security.normalize_pairing_code(body.code)
        check_rate("pair:" + code[:2])
        h = security.sha256(code)
        now = time.time()
        # Marcar como usado de forma atómica: solo una petición puede ganar.
        cur = db.execute(
            "UPDATE pairing_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
            (now, h, now),
        )
        if cur.rowcount != 1:
            login_failures["pair:" + code[:2]].append(now)
            row = db.one("SELECT used_at, expires_at FROM pairing_codes WHERE code_hash = ?", (h,))
            if row is None:
                raise HTTPException(400, "Código no válido.")
            if row["used_at"]:
                raise HTTPException(400, "Este código ya se usó. Genera uno nuevo en la laptop.")
            raise HTTPException(400, "El código caducó. Genera uno nuevo en la laptop.")
        row = db.one("SELECT user_id, laptop_id FROM pairing_codes WHERE code_hash = ?", (h,))
        device_id = security.new_device_id()
        db.execute(
            "INSERT INTO devices (id, user_id, kind, name, created_at) VALUES (?, ?, 'phone', ?, ?)",
            (device_id, row["user_id"], body.device_name.strip() or "Teléfono", now),
        )
        return issue_tokens(device_id, row["user_id"], "phone")

    # ------------------------------------------------ dispositivos

    @app.get("/api/devices")
    def list_devices(p: Principal = Depends(current)) -> dict:
        rows = db.all(
            "SELECT id, kind, name, alerts_enabled, push_sub IS NOT NULL AS has_push, created_at, last_seen "
            "FROM devices WHERE user_id = ? ORDER BY created_at",
            (p.user_id,),
        )
        devices = []
        for r in rows:
            d = dict(r)
            d["alerts_enabled"] = bool(d["alerts_enabled"])
            d["has_push"] = bool(d["has_push"])
            d["is_me"] = r["id"] == p.device_id
            d["connected"] = r["id"] in hub.sockets
            if r["kind"] == "laptop":
                d["status"] = hub.laptop_state(r["id"])
            devices.append(d)
        return {"devices": devices}

    @app.delete("/api/devices/{device_id}")
    async def revoke_device(device_id: str, p: Principal = Depends(current)) -> dict:
        row = db.one("SELECT user_id FROM devices WHERE id = ?", (device_id,))
        if row is None or row["user_id"] != p.user_id:
            raise HTTPException(404, "Dispositivo no encontrado.")
        db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        await hub.disconnect(device_id, "revoked")
        return {"ok": True}

    @app.patch("/api/devices/me")
    def update_prefs(body: PrefsIn, p: Principal = Depends(phone_only)) -> dict:
        db.execute("UPDATE devices SET alerts_enabled = ? WHERE id = ?", (int(body.alerts_enabled), p.device_id))
        return {"alerts_enabled": body.alerts_enabled}

    @app.put("/api/devices/me/push")
    def set_push(body: PushSubIn, p: Principal = Depends(phone_only)) -> dict:
        db.execute("UPDATE devices SET push_sub = ? WHERE id = ?", (body.model_dump_json(), p.device_id))
        return {"ok": True}

    @app.delete("/api/devices/me/push")
    def clear_push(p: Principal = Depends(phone_only)) -> dict:
        db.execute("UPDATE devices SET push_sub = NULL WHERE id = ?", (p.device_id,))
        return {"ok": True}

    # ------------------------------------------------ eventos de movimiento

    @app.post("/api/events/motion")
    def motion(body: MotionIn, p: Principal = Depends(laptop_only)) -> dict:
        now = time.time()
        ts = body.ts if body.ts and abs(body.ts - now) < 300 else now
        db.execute("INSERT INTO events (user_id, laptop_id, ts) VALUES (?, ?, ?)", (p.user_id, p.device_id, ts))
        db.purge_old(settings.event_retention_days)

        if now - last_push.get(p.device_id, 0) < settings.min_push_interval_seconds:
            return {"stored": True, "notified": 0, "throttled": True}
        last_push[p.device_id] = now

        laptop = db.one("SELECT name FROM devices WHERE id = ?", (p.device_id,))
        payload = {
            "type": "motion",
            "ts": ts,
            "laptop_id": p.device_id,
            "laptop_name": laptop["name"] if laptop else "Laptop",
            # Enlace a la app; abrirlo exige que el teléfono esté emparejado y con sesión válida.
            "url": f"/app/#/live?laptop={p.device_id}",
        }
        phones = db.all(
            "SELECT id, push_sub FROM devices WHERE user_id = ? AND kind = 'phone' "
            "AND alerts_enabled = 1 AND push_sub IS NOT NULL",
            (p.user_id,),
        )
        sent = 0
        for ph in phones:
            result = pusher.send(json.loads(ph["push_sub"]), payload)
            if result == "sent":
                sent += 1
            elif result == "gone":
                db.execute("UPDATE devices SET push_sub = NULL WHERE id = ?", (ph["id"],))
        return {"stored": True, "notified": sent, "push_enabled": settings.push_enabled}

    @app.get("/api/events")
    def list_events(p: Principal = Depends(current)) -> dict:
        rows = db.all(
            "SELECT e.id, e.ts, e.laptop_id, d.name AS laptop_name FROM events e "
            "LEFT JOIN devices d ON d.id = e.laptop_id WHERE e.user_id = ? ORDER BY e.ts DESC LIMIT 200",
            (p.user_id,),
        )
        return {"events": [dict(r) for r in rows], "retention_days": settings.event_retention_days}

    @app.delete("/api/events")
    def clear_events(p: Principal = Depends(current)) -> dict:
        db.execute("DELETE FROM events WHERE user_id = ?", (p.user_id,))
        return {"ok": True}

    # ------------------------------------------------ STUN / TURN

    @app.get("/api/ice")
    def ice_servers(p: Principal = Depends(current)) -> dict:
        stun = [u.strip() for u in settings.stun_urls.split(",") if u.strip()]
        servers = [{"urls": stun}] if stun else []
        if settings.turn_enabled and not settings.turn_secret:
            servers.append({
                "urls": [u.strip() for u in settings.turn_urls.split(",") if u.strip()],
                "username": settings.turn_username,
                "credential": settings.turn_credential,
            })
            return {"ice_servers": servers, "turn": True}
        if settings.turn_enabled:
            user, cred, expires = security.turn_credentials(settings.turn_secret, p.device_id, settings.turn_ttl_seconds)
            servers.append({
                "urls": [u.strip() for u in settings.turn_urls.split(",") if u.strip()],
                "username": user,
                "credential": cred,
            })
            return {"ice_servers": servers, "expires_at": expires, "turn": True}
        return {"ice_servers": servers, "turn": False}

    # ------------------------------------------------ señalización WebRTC

    RELAY_TYPES = {"offer", "answer", "ice", "hangup", "live_error"}

    @app.websocket("/ws")
    async def signaling(ws: WebSocket) -> None:
        await ws.accept()
        # El primer mensaje debe ser {"type": "auth", "token": "..."}; así el token no viaja en la URL.
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            if first.get("type") != "auth":
                raise ValueError
            principal = authenticate(str(first.get("token", "")))
        except (HTTPException, ValueError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "Autenticación requerida."
            await ws.send_text(json.dumps({"type": "auth_error", "detail": detail}))
            await ws.close(code=4001)
            return

        dev, uid = principal.device_id, principal.user_id
        old = hub.sockets.get(dev)
        if old is not None:
            await hub.disconnect(dev, "replaced")
        hub.sockets[dev] = ws
        hub.owner[dev] = uid
        db.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), dev))
        await ws.send_text(json.dumps({"type": "auth_ok", "device_id": dev, "kind": principal.kind}))

        if principal.kind == "laptop":
            hub.presence.setdefault(dev, {})
            await hub.broadcast_presence(uid, dev)
        else:
            # Estado inicial de todas las laptops de la cuenta.
            for row in db.all("SELECT id FROM devices WHERE user_id = ? AND kind = 'laptop'", (uid,)):
                await ws.send_text(json.dumps({"type": "presence", "laptop_id": row["id"], **hub.laptop_state(row["id"])}))

        try:
            while True:
                msg = json.loads(await ws.receive_text())
                mtype = msg.get("type")
                if mtype == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif mtype == "status" and principal.kind == "laptop":
                    hub.presence[dev] = {
                        "monitoring": bool(msg.get("monitoring")),
                        "state": str(msg.get("state", "online"))[:32],
                        "camera": str(msg.get("camera", ""))[:64] or None,
                        "updated_at": time.time(),
                    }
                    await hub.broadcast_presence(uid, dev)
                elif mtype in RELAY_TYPES:
                    target = str(msg.get("to", ""))
                    if hub.owner.get(target) != uid:
                        await ws.send_text(json.dumps({"type": "error", "detail": "El dispositivo no está conectado."}))
                        continue
                    out = {k: v for k, v in msg.items() if k in ("type", "sdp", "candidate", "session", "detail")}
                    out["from"] = dev
                    await hub.send(target, out)
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass
        finally:
            if hub.sockets.get(dev) is ws:
                hub.sockets.pop(dev, None)
                hub.owner.pop(dev, None)
                if principal.kind == "laptop":
                    p = hub.presence.get(dev, {})
                    p["last_state"] = "asleep" if p.get("state") == "sleeping" else "offline"
                    p["updated_at"] = time.time()
                    await hub.broadcast_presence(uid, dev)

    # ------------------------------------------------ app del teléfono

    phone_dir = settings.phone_app_dir or os.path.join(os.path.dirname(__file__), "..", "..", "phone")
    if os.path.isfile(os.path.join(phone_dir, "index.html")):
        app.mount("/app", StaticFiles(directory=phone_dir, html=True), name="phone")

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse("/app/")

    return app

