"""Arranca el servidor RoomCam en esta misma computadora, publicado con Tailscale.

Uso (desde la carpeta server/):
    python -m roomcam_server.local                 # detecta Tailscale automáticamente
    python -m roomcam_server.local --sin-tailscale # solo en esta computadora (pruebas)

La primera vez crea un archivo .env con un secreto y las claves de notificación.
Ese archivo es privado: no lo subas a GitHub (ya está en .gitignore).
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

PORT = 8000
ENV_PATH = Path(".env")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text("utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    lines = ["# Generado por roomcam_server.local. Privado: no lo subas a GitHub."]
    lines += [f"{k}={v}" for k, v in values.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", "utf-8")


def ensure_secrets(values: dict[str, str]) -> bool:
    changed = False
    if not values.get("ROOMCAM_JWT_SECRET"):
        values["ROOMCAM_JWT_SECRET"] = secrets.token_urlsafe(48)
        changed = True
    if not values.get("ROOMCAM_VAPID_PRIVATE_KEY"):
        from .keys import generate
        pub, priv = generate()
        values["ROOMCAM_VAPID_PUBLIC_KEY"] = pub
        values["ROOMCAM_VAPID_PRIVATE_KEY"] = priv
        values.setdefault("ROOMCAM_VAPID_SUBJECT", "mailto:roomcam@example.com")
        changed = True
    values.setdefault("ROOMCAM_DB_PATH", "roomcam.db")
    return changed


def tailscale_exe() -> str | None:
    exe = shutil.which("tailscale")
    if exe:
        return exe
    win = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    return str(win) if win.exists() else None


def tailscale_url(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15)
        dns = json.loads(out.stdout)["Self"]["DNSName"].rstrip(".")
        return f"https://{dns}" if dns else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Arranca RoomCam en esta computadora.")
    parser.add_argument("--sin-tailscale", action="store_true", help="No publicar con Tailscale (solo pruebas locales).")
    args = parser.parse_args()

    values = read_env()
    if ensure_secrets(values):
        write_env(values)
        print("Se creó el archivo .env con un secreto nuevo y claves de notificación (VAPID).")

    public_url = f"http://localhost:{PORT}"
    if not args.sin_tailscale:
        exe = tailscale_exe()
        if not exe:
            print("\nNo encontré Tailscale. Instálalo desde https://tailscale.com/download e inicia sesión,\n"
                  "o ejecuta con --sin-tailscale para probar solo en esta computadora.")
            return 1
        url = tailscale_url(exe)
        if not url:
            print("\nTailscale está instalado pero esta computadora no está conectada a tu cuenta.\n"
                  "Abre PowerShell y escribe:  tailscale up\n"
                  "Abre el enlace que aparece, inicia sesión y vuelve a ejecutar este script.")
            return 1
        print(f"Publicando el servidor en tu red privada de Tailscale: {url}")
        res = subprocess.run([exe, "serve", "--bg", str(PORT)], capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            print("\nTailscale no pudo publicar el servidor. Mensaje de Tailscale:\n" + (res.stderr or res.stdout))
            print("Si menciona HTTPS, actívalo en https://login.tailscale.com/admin/dns (MagicDNS y HTTPS Certificates).")
            return 1
        public_url = url
    values["ROOMCAM_PUBLIC_URL"] = public_url
    write_env(values)

    print("\n================ RoomCam ================")
    print(f" En la app de la laptop usa:  http://localhost:{PORT}")
    print(f" En el iPhone abre:           {public_url}/app/")
    if public_url.startswith("http://"):
        print(" (Sin Tailscale el iPhone no puede conectarse; esto es solo para pruebas.)")
    print(" Para detener el servidor: Ctrl+C")
    print("=========================================\n")

    import uvicorn
    from .config import load_dotenv
    load_dotenv(str(ENV_PATH))
    uvicorn.run("roomcam_server.main:create_app", factory=True, host="127.0.0.1", port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
