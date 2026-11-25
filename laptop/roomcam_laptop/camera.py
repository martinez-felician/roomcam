"""Acceso a la cámara a través del sistema operativo.

La cámara solo se abre cuando el usuario pulsa "Iniciar vigilancia" y se libera
en cuanto pulsa "Detener". El sistema operativo muestra su propio aviso de permiso
y, en la mayoría de laptops, enciende la luz de la cámara.
"""
from __future__ import annotations

import platform
import threading
from dataclasses import dataclass

import cv2
import numpy as np

# Silenciar los avisos de OpenCV al buscar cámaras que no existen.
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass


class CameraError(Exception):
    """Error con un mensaje claro para mostrar al usuario."""


def _backend() -> int:
    system = platform.system()
    if system == "Windows":
        return cv2.CAP_DSHOW
    if system == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_V4L2


PERMISSION_HELP = {
    "Windows": "Abre Configuración > Privacidad y seguridad > Cámara y activa "
               "'Permitir que las aplicaciones de escritorio accedan a la cámara'.",
    "Darwin": "Abre Ajustes del Sistema > Privacidad y seguridad > Cámara y activa el permiso "
              "para Terminal o Python (o para RoomCam si usas la app empaquetada).",
    "Linux": "Comprueba que tu usuario pertenece al grupo 'video' (sudo usermod -aG video $USER) "
             "y que ningún otro programa está usando la cámara.",
}


def permission_help() -> str:
    return PERMISSION_HELP.get(platform.system(), PERMISSION_HELP["Linux"])


@dataclass
class CameraInfo:
    index: int
    name: str


def list_cameras(max_index: int = 5) -> list[CameraInfo]:
    """Busca cámaras disponibles. Abre cada una un instante para comprobarla y la libera."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, _backend())
        try:
            if cap.isOpened():
                found.append(CameraInfo(i, f"Cámara {i + 1}" + (" (integrada)" if i == 0 else "")))
        finally:
            cap.release()
    return found


class Camera:
    """Cámara compartida: el detector y la vista en vivo leen el último fotograma."""

    def __init__(self, index: int, width: int = 1280, height: int = 720) -> None:
        self.index = index
        self.width, self.height = width, height
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def open(self) -> None:
        cap = cv2.VideoCapture(self.index, _backend())
        if not cap.isOpened():
            cap.release()
            if not list_cameras():
                raise CameraError("No se encontró ninguna cámara. Conecta una o revisa el administrador de dispositivos.")
            raise CameraError(
                "No se pudo abrir la cámara. Puede que el permiso esté denegado o que otra app la esté usando. "
                + permission_help()
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise CameraError(
                "La cámara se abrió pero no envía imagen. Si el sistema bloqueó el permiso, "
                "la imagen llega vacía. " + permission_help()
            )
        self._cap = cap
        self._latest = frame

    def read(self) -> np.ndarray:
        if self._cap is None:
            raise CameraError("La cámara no está abierta.")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraError("Se perdió la conexión con la cámara (¿se desconectó o la tomó otra app?).")
        with self._lock:
            self._latest = frame
        return frame

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        with self._lock:
            self._latest = None
