"""Fotos instantáneas opcionales (apagadas por defecto).

Se guardan solo en esta laptop, cifradas con una clave guardada en el llavero del
sistema. Nunca se envían al servidor ni en las notificaciones.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from cryptography.fernet import Fernet, InvalidToken


@dataclass
class Snapshot:
    path: Path
    ts: float


class SnapshotStore:
    def __init__(self, folder: Path, secrets) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        key = secrets.get("snapshot_key")
        if not key:
            key = Fernet.generate_key().decode()
            secrets.set("snapshot_key", key)
        self._fernet = Fernet(key.encode())

    def save(self, frame: np.ndarray, ts: float | None = None) -> Snapshot:
        ts = ts or time.time()
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("No se pudo codificar la foto.")
        path = self.folder / f"{int(ts * 1000)}.rcsnap"
        path.write_bytes(self._fernet.encrypt(jpg.tobytes()))
        return Snapshot(path, ts)

    def list(self) -> list[Snapshot]:
        snaps = [Snapshot(p, int(p.stem) / 1000) for p in self.folder.glob("*.rcsnap") if p.stem.isdigit()]
        return sorted(snaps, key=lambda s: s.ts, reverse=True)

    def load(self, snap: Snapshot) -> np.ndarray:
        try:
            data = self._fernet.decrypt(snap.path.read_bytes())
        except InvalidToken:
            raise RuntimeError("La foto está dañada o se cifró con otra clave.")
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def delete(self, snap: Snapshot) -> None:
        snap.path.unlink(missing_ok=True)

    def delete_all(self) -> int:
        n = 0
        for s in self.list():
            self.delete(s)
            n += 1
        return n

    def purge_older_than(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        n = 0
        for s in self.list():
            if s.ts < cutoff:
                self.delete(s)
                n += 1
        return n
