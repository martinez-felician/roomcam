"""Cliente del servidor RoomCam y almacenamiento seguro de credenciales."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx

APP_NAME = "RoomCam"


def data_dir() -> Path:
    from platformdirs import user_data_dir
    p = Path(user_data_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


class SecretStore:
    """Guarda el token de renovación en el llavero del sistema
    (Administrador de credenciales en Windows, Llavero en macOS, Secret Service en Linux)."""

    def __init__(self, service: str = APP_NAME) -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        import keyring
        try:
            return keyring.get_password(self.service, key)
        except Exception:
            return None

    def set(self, key: str, value: str) -> None:
        import keyring
        keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        try:
            keyring.delete_password(self.service, key)
        except Exception:
            pass


class MemoryStore(SecretStore):
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, key):
        self._d.pop(key, None)


class ApiError(Exception):
    pass


class NetworkError(ApiError):
    pass


class AuthError(ApiError):
    pass


class ApiClient:
    def __init__(self, base_url: str, store: SecretStore | None = None, transport=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.store = store or SecretStore()
        self._http = httpx.Client(base_url=self.base_url, timeout=10, transport=transport)
        self._access: str | None = None
        self._access_exp = 0.0
        self.device_id: str | None = self.store.get("device_id")
        self._lock = threading.Lock()

    # ------------------------------------------------ peticiones

    def _request(self, method: str, path: str, *, auth: bool = True, **kw) -> dict:
        headers = kw.pop("headers", {})
        if auth:
            headers["Authorization"] = f"Bearer {self.access_token()}"
        try:
            r = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"No se pudo conectar con el servidor ({self.base_url}). Revisa tu conexión a internet "
                "o la dirección del servidor."
            ) from exc
        if r.status_code == 401:
            raise AuthError(self._detail(r))
        if r.status_code >= 400:
            raise ApiError(self._detail(r))
        return r.json() if r.content else {}

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            d = r.json().get("detail")
            return d if isinstance(d, str) else str(d)
        except Exception:
            return f"Error del servidor ({r.status_code})."

    def _save(self, tokens: dict) -> None:
        self._access = tokens["access_token"]
        self._access_exp = time.time() + tokens["expires_in"] - 30
        self.device_id = tokens["device_id"]
        self.store.set("refresh_token", tokens["refresh_token"])
        self.store.set("device_id", tokens["device_id"])
        self.store.set("server", self.base_url)

    def access_token(self) -> str:
        with self._lock:
            if self._access and time.time() < self._access_exp:
                return self._access
            refresh = self.store.get("refresh_token")
            if not refresh:
                raise AuthError("Inicia sesión para continuar.")
            try:
                tokens = self._request("POST", "/api/auth/refresh", auth=False, json={"refresh_token": refresh})
            except AuthError:
                self.forget()
                raise AuthError("Esta laptop fue revocada o su sesión caducó. Inicia sesión de nuevo.")
            self._save(tokens)
            return self._access

    @property
    def logged_in(self) -> bool:
        return bool(self.store.get("refresh_token"))

    def forget(self) -> None:
        for k in ("refresh_token", "device_id"):
            self.store.delete(k)
        self._access = None
        self.device_id = None

    # ------------------------------------------------ operaciones

    def register(self, email: str, password: str) -> None:
        self._request("POST", "/api/auth/register", auth=False, json={"email": email, "password": password})

    def login(self, email: str, password: str, device_name: str) -> None:
        tokens = self._request(
            "POST", "/api/auth/login", auth=False,
            json={"email": email, "password": password, "device_name": device_name},
        )
        self._save(tokens)

    def logout(self) -> None:
        """Cierra sesión y revoca esta laptop en el servidor."""
        try:
            if self.device_id:
                self._request("DELETE", f"/api/devices/{self.device_id}")
        except ApiError:
            pass
        self.forget()

    def pairing_code(self) -> dict:
        return self._request("POST", "/api/pairing/codes")

    def devices(self) -> list[dict]:
        return self._request("GET", "/api/devices")["devices"]

    def revoke(self, device_id: str) -> None:
        self._request("DELETE", f"/api/devices/{device_id}")

    def report_motion(self, ts: float) -> dict:
        return self._request("POST", "/api/events/motion", json={"ts": ts})

    def ice_servers(self) -> list[dict]:
        return self._request("GET", "/api/ice")["ice_servers"]

    def delete_account(self, password: str) -> None:
        self._request("DELETE", "/api/account", json={"password": password})
        self.forget()

    def ws_url(self) -> str:
        return self.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"


class Settings:
    """Preferencias locales (no secretas) en un archivo JSON."""

    DEFAULTS = {
        "server_url": "http://localhost:8000",
        "camera_index": 0,
        "sensitivity": 50,
        "cooldown_seconds": 60,
        "zone": None,
        "snapshots_enabled": False,
        "snapshot_retention_days": 7,
        "prevent_sleep": False,
    }

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "settings.json"
        self.values = dict(self.DEFAULTS)
        try:
            self.values.update(json.loads(self.path.read_text("utf-8")))
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.values[k]

    def __setitem__(self, k, v):
        self.values[k] = v
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.values, indent=2), "utf-8")
