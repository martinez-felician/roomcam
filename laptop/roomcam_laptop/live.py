"""Conexión en tiempo real con el servidor y vista en vivo por WebRTC.

- Mantiene un WebSocket saliente y autenticado con el servidor (sin puertos de entrada).
- Informa del estado (vigilando, detenida, dormida).
- Cuando un teléfono emparejado pide la vista en vivo y la vigilancia está activa,
  crea una conexión WebRTC. El vídeo va cifrado con DTLS-SRTP directo al teléfono
  (o a través de TURN, que no puede descifrarlo).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Callable

import cv2
import numpy as np
import websockets
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from .api import ApiClient, ApiError, AuthError

log = logging.getLogger("roomcam.live")

LIVE_WIDTH = 640
LIVE_FPS = 15


class CameraTrack(VideoStreamTrack):
    """Pista de vídeo que lee el último fotograma de la cámara compartida."""

    def __init__(self, frame_source: Callable[[], np.ndarray | None]) -> None:
        super().__init__()
        self._source = frame_source
        self._black = np.zeros((360, 640, 3), np.uint8)

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()
        frame = self._source()
        if frame is None:
            frame = self._black
        elif frame.shape[1] > LIVE_WIDTH:
            h = int(frame.shape[0] * LIVE_WIDTH / frame.shape[1]) // 2 * 2
            frame = cv2.resize(frame, (LIVE_WIDTH, h))
        vf = VideoFrame.from_ndarray(frame, format="bgr24")
        vf.pts, vf.time_base = pts, time_base
        return vf


def _ice_config(servers: list[dict]) -> RTCConfiguration:
    out = []
    for s in servers:
        urls = s["urls"] if isinstance(s["urls"], list) else [s["urls"]]
        out.append(RTCIceServer(urls=urls, username=s.get("username"), credential=s.get("credential")))
    return RTCConfiguration(iceServers=out)


class LiveAgent:
    """Corre en su propio hilo con un bucle asyncio. Los métodos públicos son seguros desde la GUI."""

    def __init__(
        self,
        api: ApiClient,
        frame_source: Callable[[], np.ndarray | None],
        on_state: Callable[[str, str], None] = lambda kind, msg: None,
        on_viewers: Callable[[int], None] = lambda n: None,
    ) -> None:
        self.api = api
        self.frame_source = frame_source
        self.on_state = on_state
        self.on_viewers = on_viewers
        self.monitoring = False
        self.camera_name = ""
        self.peers: dict[str, RTCPeerConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._stop = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------ control desde la GUI

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="roomcam-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)

    def set_monitoring(self, monitoring: bool, camera_name: str = "", state: str | None = None) -> None:
        self.monitoring = monitoring
        self.camera_name = camera_name
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._after_monitoring_change(state), self._loop)

    def notify_sleep(self) -> None:
        """Avisar al servidor de que la laptop va a dormir (si el sistema lo informa)."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._send_status("sleeping"), self._loop)

    # ------------------------------------------------ bucle interno

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()
            self._loop = None

    async def _main(self) -> None:
        delay = 1
        while not self._stop:
            try:
                await self._session()
                delay = 1
            except AuthError as exc:
                self.on_state("error", str(exc))
                return
            except (OSError, websockets.WebSocketException, ApiError) as exc:
                log.info("Conexión perdida: %s", exc)
                self.on_state("offline", f"Sin conexión con el servidor. Reintentando en {delay} s…")
            await self._close_peers()
            if self._stop:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _session(self) -> None:
        token = await asyncio.to_thread(self.api.access_token)
        async with websockets.connect(self.api.ws_url(), ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            reply = json.loads(await ws.recv())
            if reply.get("type") != "auth_ok":
                raise AuthError(reply.get("detail", "El servidor rechazó la conexión."))
            self._ws = ws
            self.on_state("online", "Conectado al servidor.")
            await self._send_status()
            try:
                async for raw in ws:
                    await self._handle(json.loads(raw))
            finally:
                self._ws = None

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        sender = msg.get("from", "")
        if t == "closed":
            raise AuthError(
                "Esta laptop fue revocada desde otro dispositivo." if msg.get("reason") == "revoked"
                else "La cuenta fue eliminada." if msg.get("reason") == "account_deleted"
                else "Sesión cerrada por el servidor."
            )
        if t == "offer":
            await self._answer(sender, msg.get("sdp", ""))
        elif t == "hangup":
            await self._close_peer(sender)

    async def _answer(self, phone_id: str, sdp: str) -> None:
        if not self.monitoring:
            await self._send({"type": "live_error", "to": phone_id,
                              "detail": "La vigilancia está detenida. Iníciala en la laptop para ver en vivo."})
            return
        await self._close_peer(phone_id)
        try:
            servers = await asyncio.to_thread(self.api.ice_servers)
        except ApiError:
            servers = [{"urls": ["stun:stun.l.google.com:19302"]}]
        pc = RTCPeerConnection(_ice_config(servers))
        self.peers[phone_id] = pc

        @pc.on("connectionstatechange")
        async def _on_change() -> None:
            if pc.connectionState in ("failed", "closed"):
                await self._close_peer(phone_id)
            self.on_viewers(sum(1 for p in self.peers.values() if p.connectionState == "connected"))

        pc.addTrack(CameraTrack(self.frame_source))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)  # aiortc espera a reunir los candidatos ICE
        await self._send({"type": "answer", "to": phone_id, "sdp": pc.localDescription.sdp})

    async def _close_peer(self, phone_id: str) -> None:
        pc = self.peers.pop(phone_id, None)
        if pc is not None:
            await pc.close()
        self.on_viewers(len(self.peers))

    async def _close_peers(self) -> None:
        for pid in list(self.peers):
            await self._close_peer(pid)

    async def _after_monitoring_change(self, state: str | None) -> None:
        if not self.monitoring:
            # Al detener la vigilancia se corta cualquier vista en vivo de inmediato.
            for pid in list(self.peers):
                await self._send({"type": "hangup", "to": pid})
            await self._close_peers()
        await self._send_status(state)

    async def _send_status(self, state: str | None = None) -> None:
        await self._send({
            "type": "status",
            "monitoring": self.monitoring,
            "state": state or ("monitoring" if self.monitoring else "stopped"),
            "camera": self.camera_name if self.monitoring else "",
        })

    async def _send(self, msg: dict) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(msg))
            except websockets.WebSocketException:
                pass

    async def _shutdown(self) -> None:
        await self._close_peers()
        if self._ws is not None:
            await self._ws.close()
