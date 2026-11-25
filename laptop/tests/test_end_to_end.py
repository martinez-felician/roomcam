"""Prueba de extremo a extremo: servidor real + agente de la laptop + un "teléfono" WebRTC.

Comprueba emparejamiento, señalización autenticada, vídeo en vivo cifrado,
rechazo cuando la vigilancia está detenida y corte inmediato al revocar.
"""
import asyncio
import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest
import uvicorn
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

from roomcam_laptop.api import ApiClient, MemoryStore
from roomcam_laptop.live import LiveAgent
from roomcam_server.config import Settings
from roomcam_server.main import create_app


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    settings = Settings()
    settings.database_path = str(tmp_path / "e2e.db")
    settings.jwt_secret = "e2e-" + "y" * 40
    settings.stun_urls = ""
    settings.phone_app_dir = str(tmp_path / "none")
    port = free_port()
    settings.public_url = f"http://127.0.0.1:{port}"
    cfg = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield settings.public_url
    srv.should_exit = True
    t.join(5)


def frame_source():
    f = np.zeros((360, 640, 3), np.uint8)
    f[:, :, 2] = 200  # rojo
    return f


async def phone_view(base, phone_tokens, laptop_id):
    """Teléfono simulado: pide la vista en vivo y devuelve el primer fotograma o el error."""
    async with websockets.connect(base.replace("http", "ws") + "/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": phone_tokens["access_token"]}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        got_track = asyncio.get_event_loop().create_future()
        pc.on("track", lambda track: got_track.done() or got_track.set_result(track))
        await pc.setLocalDescription(await pc.createOffer())
        await ws.send(json.dumps({"type": "offer", "to": laptop_id, "sdp": pc.localDescription.sdp}))
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if msg["type"] == "live_error":
                    return {"error": msg["detail"]}
                if msg["type"] == "answer":
                    await pc.setRemoteDescription(RTCSessionDescription(msg["sdp"], "answer"))
                    track = await asyncio.wait_for(got_track, 15)
                    frame = await asyncio.wait_for(track.recv(), 15)
                    img = frame.to_ndarray(format="bgr24")
                    return {"frame": img, "dtls": pc.sctp is None and pc.connectionState}
        finally:
            await pc.close()


def test_pair_alert_and_live_view(server):
    laptop = ApiClient(server, MemoryStore())
    laptop.register("fabian@example.com", "contraseña-larga")
    laptop.login("fabian@example.com", "contraseña-larga", "HP")

    code = laptop.pairing_code()["code"]
    phone = httpx.post(server + "/api/pairing/claim", json={"code": code, "device_name": "iPhone"}).json()
    assert phone["kind"] == "phone"

    states = []
    agent = LiveAgent(laptop, frame_source, on_state=lambda k, m: states.append(k))
    agent.start()
    try:
        for _ in range(100):
            if "online" in states:
                break
            time.sleep(0.05)
        assert "online" in states

        # Vigilancia detenida: la vista en vivo se rechaza con un mensaje claro.
        result = asyncio.run(phone_view(server, phone, laptop.device_id))
        assert "detenida" in result["error"]

        # Vigilancia activa: llega vídeo real por WebRTC (DTLS-SRTP).
        agent.set_monitoring(True, "Cámara de prueba")
        time.sleep(0.3)
        result = asyncio.run(phone_view(server, phone, laptop.device_id))
        frame = result["frame"]
        assert frame.shape[1] == 640
        assert frame[:, :, 2].mean() > 150 and frame[:, :, 0].mean() < 60

        # La alerta queda registrada y el teléfono la ve en su historial.
        assert laptop.report_motion(time.time())["stored"]
        auth = {"Authorization": f"Bearer {phone['access_token']}"}
        events = httpx.get(server + "/api/events", headers=auth).json()["events"]
        assert len(events) == 1

        # Revocar el teléfono: pierde el acceso al instante.
        laptop.revoke(phone["device_id"])
        assert httpx.get(server + "/api/devices", headers=auth).status_code == 401
    finally:
        agent.stop()
