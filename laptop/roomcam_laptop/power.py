"""Evitar que la laptop se duerma mientras vigila (solo si el usuario lo activa)."""
from __future__ import annotations

import platform
import subprocess


class SleepBlocker:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.active = False

    def enable(self) -> bool:
        system = platform.system()
        try:
            if system == "Windows":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            elif system == "Darwin":
                self._proc = subprocess.Popen(["caffeinate", "-i"])
            else:
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=sleep:idle", "--who=RoomCam",
                     "--why=Vigilancia activa", "sleep", "infinity"]
                )
            self.active = True
        except Exception:
            self.active = False
        return self.active

    def disable(self) -> None:
        if platform.system() == "Windows" and self.active:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        if self._proc:
            self._proc.terminate()
            self._proc = None
        self.active = False
