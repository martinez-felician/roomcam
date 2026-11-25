"""Configuración del servidor, leída de variables de entorno."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # Secreto para firmar los tokens de acceso (JWT). Obligatorio en producción.
    jwt_secret: str = field(default_factory=lambda: _env("ROOMCAM_JWT_SECRET"))
    database_path: str = field(default_factory=lambda: _env("ROOMCAM_DB_PATH", "roomcam.db"))
    # URL pública del servidor, usada en los enlaces de las notificaciones.
    public_url: str = field(default_factory=lambda: _env("ROOMCAM_PUBLIC_URL", "http://localhost:8000"))

    access_token_minutes: int = field(default_factory=lambda: int(_env("ROOMCAM_ACCESS_MINUTES", "15")))
    refresh_token_days: int = field(default_factory=lambda: int(_env("ROOMCAM_REFRESH_DAYS", "30")))
    pairing_code_minutes: int = field(default_factory=lambda: int(_env("ROOMCAM_PAIRING_MINUTES", "5")))
    event_retention_days: int = field(default_factory=lambda: int(_env("ROOMCAM_EVENT_RETENTION_DAYS", "30")))
    # Tiempo mínimo entre notificaciones de una misma laptop (protección del servidor).
    min_push_interval_seconds: int = field(default_factory=lambda: int(_env("ROOMCAM_MIN_PUSH_SECONDS", "10")))

    # Web Push (VAPID). Genera las claves con: python -m roomcam_server.keys
    vapid_public_key: str = field(default_factory=lambda: _env("ROOMCAM_VAPID_PUBLIC_KEY"))
    vapid_private_key: str = field(default_factory=lambda: _env("ROOMCAM_VAPID_PRIVATE_KEY"))
    vapid_subject: str = field(default_factory=lambda: _env("ROOMCAM_VAPID_SUBJECT", "mailto:admin@example.com"))

    # STUN/TURN. Con TURN_SECRET se generan credenciales temporales (estilo coturn "use-auth-secret").
    stun_urls: str = field(default_factory=lambda: _env("ROOMCAM_STUN_URLS", "stun:stun.l.google.com:19302"))
    turn_urls: str = field(default_factory=lambda: _env("ROOMCAM_TURN_URLS"))
    turn_secret: str = field(default_factory=lambda: _env("ROOMCAM_TURN_SECRET"))
    turn_ttl_seconds: int = field(default_factory=lambda: int(_env("ROOMCAM_TURN_TTL", "3600")))

    # Carpeta de la app del teléfono (PWA) que el servidor sirve en /app.
    phone_app_dir: str = field(default_factory=lambda: _env("ROOMCAM_PHONE_DIR", ""))

    def __post_init__(self) -> None:
        if not self.jwt_secret:
            # Solo para desarrollo: un secreto aleatorio invalida los tokens al reiniciar.
            self.jwt_secret = secrets.token_urlsafe(48)
            self.dev_secret = True
        else:
            self.dev_secret = False

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def turn_enabled(self) -> bool:
        return bool(self.turn_urls and self.turn_secret)
