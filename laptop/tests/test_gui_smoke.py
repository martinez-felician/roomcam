"""Comprueba que la ventana se construye y que iniciar/detener libera la cámara (sin pantalla)."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from roomcam_laptop import gui  # noqa: E402
from roomcam_laptop.api import MemoryStore, Settings  # noqa: E402
from roomcam_laptop.camera import CameraInfo  # noqa: E402


class FakeCamera:
    opened = 0
    released = 0

    def __init__(self, index):
        self._open = False

    @property
    def is_open(self):
        return self._open

    def open(self):
        FakeCamera.opened += 1
        self._open = True

    def read(self):
        return np.zeros((360, 640, 3), np.uint8)

    def latest(self):
        return self.read() if self._open else None

    def release(self):
        if self._open:
            FakeCamera.released += 1
        self._open = False


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui, "SecretStore", MemoryStore)
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "list_cameras", lambda: [CameraInfo(0, "Cámara 1 (integrada)")])
    monkeypatch.setattr(gui, "Camera", FakeCamera)
    win = gui.MainWindow(Settings(tmp_path / "s.json"), api=None, demo=True)
    yield win, app
    win.close()


def test_demo_window_start_stop_releases_camera(window):
    win, app = window
    assert "DEMO" in win.windowTitle()
    win.start_monitoring()
    assert "VIGILANDO" in win.monitor_chip.text()
    for _ in range(20):
        app.processEvents()
    win.stop_monitoring()
    assert "detenida" in win.monitor_chip.text()
    assert FakeCamera.released == FakeCamera.opened == 1
    assert "SIMULADO" in win.device_list.item(0).text()
