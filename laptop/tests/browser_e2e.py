"""Prueba manual de extremo a extremo con un navegador real (Chromium + Playwright).

Levanta el servidor con la app del teléfono, conecta el agente de la laptop con
fotogramas sintéticos, empareja la PWA en Chromium y comprueba que llega vídeo.

Uso:  python tests/browser_e2e.py [carpeta_para_capturas]
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "laptop"), str(ROOT / "server")]

from roomcam_laptop.api import ApiClient, MemoryStore  # noqa: E402
from roomcam_laptop.live import LiveAgent  # noqa: E402
from roomcam_server.config import Settings  # noqa: E402
from roomcam_server.main import create_app  # noqa: E402


def frame_source():
    f = np.zeros((360, 640, 3), np.uint8)
    f[:, :, 1] = 180
    t = int(time.time() * 10) % 600
    f[100:260, t:t + 40] = 255
    return f


async def main(shots: Path) -> None:
    from playwright.async_api import async_playwright

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    settings = Settings()
    settings.database_path = str(Path(tempfile.mkdtemp()) / "b.db")
    settings.jwt_secret = "browser-" + "z" * 40
    settings.public_url = f"http://localhost:{port}"
    settings.phone_app_dir = str(ROOT / "phone")
    settings.stun_urls = ""
    srv = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        await asyncio.sleep(0.05)
    base = settings.public_url

    laptop = ApiClient(base, MemoryStore())
    laptop.register("fabian@example.com", "contraseña-larga")
    laptop.login("fabian@example.com", "contraseña-larga", "Laptop HP")
    agent = LiveAgent(laptop, frame_source)
    agent.start()
    code = laptop.pairing_code()["code"]

    async with async_playwright() as p:
        exe = os.environ.get("ROOMCAM_CHROMIUM")
        browser = await p.chromium.launch(executable_path=exe) if exe else await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page = await ctx.new_page()
        page.on("console", lambda m: print("[phone]", m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: print("[pageerror]", e))
        await page.goto(base + "/app/")
        await page.fill("#code", code)
        await page.click("#pair")
        await page.wait_for_selector("text=Alertas de movimiento")
        await page.wait_for_selector("text=Vigilancia detenida")
        await page.screenshot(path=str(shots / "phone-stopped.png"))
        print("OK emparejado; laptop conectada con vigilancia detenida")

        agent.set_monitoring(True, "Cámara integrada")
        await page.wait_for_selector("button[data-live]:not([disabled])")
        await page.screenshot(path=str(shots / "phone-home.png"))
        await page.click("button[data-live]")
        try:
            await page.wait_for_function(
                "() => { const v = document.getElementById('live-video'); return v && v.videoWidth > 0 && !v.paused; }",
                timeout=30000,
            )
        except Exception:
            await page.screenshot(path=str(shots / "phone-live-fail.png"))
            print("FALLO:", await page.evaluate("document.getElementById('live-overlay')?.textContent"))
            raise
        await asyncio.sleep(1)
        size = await page.evaluate("[document.getElementById('live-video').videoWidth, document.getElementById('live-video').videoHeight]")
        await page.screenshot(path=str(shots / "phone-live.png"))
        print("OK vídeo en vivo recibido en el navegador:", size)

        agent.set_monitoring(False)
        await page.wait_for_selector("text=La vigilancia se detuvo", timeout=10000)
        await page.screenshot(path=str(shots / "phone-stopped-live.png"))
        print("OK al detener la vigilancia el teléfono cierra la vista en vivo")
        await browser.close()
    agent.stop()
    srv.should_exit = True


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
    out.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(out))
    print("Capturas en", out)
