"""Detección de movimiento local. Las imágenes nunca salen de este módulo."""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MotionSettings:
    # 1 (poco sensible) a 100 (muy sensible).
    sensitivity: int = 50
    # Segundos mínimos entre dos alertas (control de falsas alarmas).
    cooldown_seconds: int = 60
    # Fotogramas seguidos con movimiento antes de alertar (ignora parpadeos de luz).
    confirm_frames: int = 3
    # Zona de detección normalizada (x, y, ancho, alto) entre 0 y 1. None = toda la imagen.
    zone: tuple[float, float, float, float] | None = None

    def pixel_threshold(self) -> int:
        # Diferencia de brillo por píxel para contar como "cambio".
        return int(np.interp(self.sensitivity, [1, 100], [60, 12]))

    def min_area_fraction(self) -> float:
        # Fracción de la zona que debe cambiar.
        return float(np.interp(self.sensitivity, [1, 100], [0.08, 0.004]))


@dataclass
class MotionResult:
    score: float          # fracción de la zona con cambio
    moving: bool          # supera el umbral en este fotograma
    alert: bool           # hay que avisar (confirmado y fuera del tiempo de espera)
    boxes: list[tuple[int, int, int, int]]


class MotionDetector:
    def __init__(self, settings: MotionSettings | None = None, clock=time.monotonic) -> None:
        self.settings = settings or MotionSettings()
        self._clock = clock
        self._background: np.ndarray | None = None
        self._streak = 0
        self._last_alert = -1e9

    def reset(self) -> None:
        self._background = None
        self._streak = 0

    def _zone_rect(self, shape) -> tuple[int, int, int, int]:
        h, w = shape[:2]
        if not self.settings.zone:
            return 0, 0, w, h
        x, y, zw, zh = self.settings.zone
        x0, y0 = int(max(0, min(1, x)) * w), int(max(0, min(1, y)) * h)
        x1, y1 = int(max(0, min(1, x + zw)) * w), int(max(0, min(1, y + zh)) * h)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return 0, 0, w, h
        return x0, y0, x1 - x0, y1 - y0

    def process(self, frame_bgr: np.ndarray) -> MotionResult:
        small = cv2.resize(frame_bgr, (320, int(320 * frame_bgr.shape[0] / frame_bgr.shape[1])))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._background is None:
            self._background = gray.astype("float32")
            return MotionResult(0.0, False, False, [])

        # Fondo que se adapta despacio a cambios graduales de luz.
        cv2.accumulateWeighted(gray, self._background, 0.05)
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self._background))
        _, mask = cv2.threshold(diff, self.settings.pixel_threshold(), 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, None, iterations=2)

        x, y, w, h = self._zone_rect(mask.shape)
        zone_mask = mask[y:y + h, x:x + w]
        score = float(np.count_nonzero(zone_mask)) / max(1, zone_mask.size)
        moving = score >= self.settings.min_area_fraction()

        boxes: list[tuple[int, int, int, int]] = []
        if moving:
            scale = frame_bgr.shape[1] / small.shape[1]
            contours, _ = cv2.findContours(zone_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                bx, by, bw, bh = cv2.boundingRect(c)
                if bw * bh > 50:
                    boxes.append(tuple(int(v * scale) for v in (bx + x, by + y, bw, bh)))

        self._streak = self._streak + 1 if moving else 0
        alert = False
        now = self._clock()
        if self._streak >= self.settings.confirm_frames and now - self._last_alert >= self.settings.cooldown_seconds:
            alert = True
            self._last_alert = now
            self._streak = 0
        return MotionResult(score, moving, alert, boxes)

    def seconds_until_ready(self) -> float:
        return max(0.0, self.settings.cooldown_seconds - (self._clock() - self._last_alert))
