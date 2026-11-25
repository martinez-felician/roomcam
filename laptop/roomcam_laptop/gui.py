"""Ventana principal de RoomCam para la laptop (PySide6)."""
from __future__ import annotations

import datetime as dt
import io
import platform
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import qrcode
from PySide6.QtCore import QObject, QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSlider, QSpinBox, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget, QMenu,
)

from .api import ApiClient, ApiError, AuthError, NetworkError, SecretStore, Settings, data_dir
from .camera import Camera, CameraError, list_cameras
from .live import LiveAgent
from .motion import MotionDetector, MotionSettings
from .power import SleepBlocker
from .snapshots import SnapshotStore

RED, GREEN, GREY, AMBER = "#d93025", "#188038", "#5f6368", "#b06000"


def fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S")


def to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


# ---------------------------------------------------------------- captura en segundo plano


class CaptureWorker(QObject):
    frame = Signal(QImage, float, list)   # imagen, puntuación de movimiento, cajas
    alert = Signal(float)                 # hora del evento
    error = Signal(str)
    stopped = Signal()

    def __init__(self, camera: Camera, detector: MotionDetector) -> None:
        super().__init__()
        self.camera = camera
        self.detector = detector
        self._running = False

    def run(self) -> None:
        self._running = True
        try:
            self.camera.open()
        except CameraError as exc:
            self.error.emit(str(exc))
            self._running = False
            self.stopped.emit()
            return
        self.detector.reset()
        try:
            while self._running:
                t0 = time.monotonic()
                frame = self.camera.read()
                result = self.detector.process(frame)
                if result.alert:
                    self.alert.emit(time.time())
                self.frame.emit(to_qimage(frame), result.score, result.boxes)
                time.sleep(max(0.0, 1 / 15 - (time.monotonic() - t0)))
        except CameraError as exc:
            self.error.emit(str(exc))
        finally:
            # Liberar la cámara siempre, pase lo que pase.
            self.camera.release()
            self._running = False
            self.stopped.emit()

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------- vista previa con zona


class PreviewLabel(QLabel):
    zone_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#202124;color:#e8eaed;border-radius:8px")
        self.zone: tuple[float, float, float, float] | None = None
        self.boxes: list = []
        self._img: QImage | None = None
        self._drag: QPoint | None = None
        self._drag_now: QPoint | None = None
        self.show_placeholder()

    def show_placeholder(self, text: str = "Cámara apagada.\nPulsa «Iniciar vigilancia» para encenderla.") -> None:
        self._img = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def _image_rect(self) -> QRect | None:
        if self._img is None:
            return None
        scaled = self._img.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def set_frame(self, img: QImage, boxes: list) -> None:
        self._img = img
        self.boxes = boxes
        self.setText("")
        self.update()

    def paintEvent(self, event) -> None:
        if self._img is None:
            return super().paintEvent(event)
        r = self._image_rect()
        p = QPainter(self)
        p.drawImage(r, self._img)
        sx = r.width() / self._img.width()
        sy = r.height() / self._img.height()
        p.setPen(QPen(QColor(RED), 2))
        for (x, y, w, h) in self.boxes:
            p.drawRect(int(r.x() + x * sx), int(r.y() + y * sy), int(w * sx), int(h * sy))
        if self.zone:
            zx, zy, zw, zh = self.zone
            p.setPen(QPen(QColor("#fbbc04"), 2, Qt.DashLine))
            p.drawRect(int(r.x() + zx * r.width()), int(r.y() + zy * r.height()), int(zw * r.width()), int(zh * r.height()))
        if self._drag and self._drag_now:
            p.setPen(QPen(QColor("#fbbc04"), 1, Qt.DotLine))
            p.drawRect(QRect(self._drag, self._drag_now).normalized())
        p.end()

    def mousePressEvent(self, e) -> None:
        if self._img is not None:
            self._drag = self._drag_now = e.position().toPoint()

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            self._drag_now = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        r = self._image_rect()
        if self._drag and r:
            sel = QRect(self._drag, e.position().toPoint()).normalized().intersected(r)
            if sel.width() > 20 and sel.height() > 20:
                self.zone = (
                    (sel.x() - r.x()) / r.width(), (sel.y() - r.y()) / r.height(),
                    sel.width() / r.width(), sel.height() / r.height(),
                )
                self.zone_changed.emit(self.zone)
        self._drag = self._drag_now = None
        self.update()


# ---------------------------------------------------------------- pantalla de configuración


class SetupDialog(QDialog):
    """Primera pantalla: modo demo o conexión a un servidor real."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurar RoomCam")
        self.settings = settings
        self.api: ApiClient | None = None
        self.demo = False

        lay = QVBoxLayout(self)
        intro = QLabel(
            "<h2>Bienvenido a RoomCam</h2>"
            "<p>RoomCam vigila con la cámara de esta laptop y avisa a tu teléfono cuando detecta movimiento. "
            "La imagen se analiza aquí mismo; no se graba nada por defecto.</p>"
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

        demo_box = QGroupBox("Probar sin cuenta")
        dl = QVBoxLayout(demo_box)
        dl.addWidget(QLabel("El modo demo usa tu cámara de verdad, pero el emparejamiento y las "
                            "notificaciones son <b>simulados</b> y no salen de la laptop."))
        demo_btn = QPushButton("Entrar en modo demo")
        demo_btn.clicked.connect(self._demo)
        dl.addWidget(demo_btn)
        lay.addWidget(demo_box)

        real = QGroupBox("Conectar a tu servidor")
        form = QFormLayout(real)
        self.server = QLineEdit(settings["server_url"])
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.name = QLineEdit(socket.gethostname() or "Laptop")
        form.addRow("Servidor", self.server)
        form.addRow("Correo", self.email)
        form.addRow("Contraseña", self.password)
        form.addRow("Nombre de esta laptop", self.name)
        row = QHBoxLayout()
        login = QPushButton("Iniciar sesión")
        login.setDefault(True)
        login.clicked.connect(lambda: self._connect(create=False))
        create = QPushButton("Crear cuenta")
        create.clicked.connect(lambda: self._connect(create=True))
        row.addWidget(login)
        row.addWidget(create)
        form.addRow(row)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color:{RED}")
        form.addRow(self.error)
        lay.addWidget(real)

    def _demo(self) -> None:
        self.demo = True
        self.accept()

    def _connect(self, create: bool) -> None:
        url = self.server.text().strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            self.error.setText("La dirección del servidor debe empezar por https:// (o http:// en pruebas locales).")
            return
        email, pw = self.email.text().strip(), self.password.text()
        if create and len(pw) < 10:
            self.error.setText("La contraseña debe tener al menos 10 caracteres.")
            return
        api = ApiClient(url)
        try:
            if create:
                api.register(email, pw)
            api.login(email, pw, self.name.text().strip() or "Laptop")
        except NetworkError as exc:
            self.error.setText(str(exc))
            return
        except ApiError as exc:
            self.error.setText(str(exc))
            return
        self.settings["server_url"] = url
        self.api = api
        self.accept()


class PairingDialog(QDialog):
    def __init__(self, code: str, url: str, expires_at: float, demo: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Emparejar teléfono" + (" (SIMULADO)" if demo else ""))
        self.expires_at = expires_at
        lay = QVBoxLayout(self)
        if demo:
            warn = QLabel("MODO DEMO: este código es de prueba y no empareja ningún teléfono real.")
            warn.setStyleSheet(f"background:{AMBER};color:white;padding:6px;border-radius:4px")
            warn.setWordWrap(True)
            lay.addWidget(warn)
        lay.addWidget(QLabel("1. En tu teléfono abre la app RoomCam.\n"
                             "2. Escanea este QR con la cámara o escribe el código."))
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pm = QPixmap()
        pm.loadFromData(buf.getvalue())
        qr = QLabel()
        qr.setPixmap(pm.scaled(260, 260, Qt.KeepAspectRatio))
        qr.setAlignment(Qt.AlignCenter)
        lay.addWidget(qr)
        c = QLabel(code)
        c.setAlignment(Qt.AlignCenter)
        c.setStyleSheet("font-size:28px;font-weight:bold;letter-spacing:4px")
        c.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(c)
        self.timer_label = QLabel()
        self.timer_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.timer_label)
        lay.addWidget(QLabel("El código caduca y solo se puede usar una vez."))
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._t = QTimer(self, interval=1000, timeout=self._tick)
        self._t.start()
        self._tick()

    def _tick(self) -> None:
        left = int(self.expires_at - time.time())
        if left <= 0:
            self.timer_label.setText("Código caducado. Cierra y genera uno nuevo.")
            self.timer_label.setStyleSheet(f"color:{RED}")
            self._t.stop()
        else:
            self.timer_label.setText(f"Caduca en {left // 60}:{left % 60:02d}")


# ---------------------------------------------------------------- ventana principal


class MainWindow(QMainWindow):
    status_changed = Signal(str, str)
    viewers_changed = Signal(int)
    motion_reported = Signal(str, bool)

    def __init__(self, settings: Settings, api: ApiClient | None, demo: bool) -> None:
        super().__init__()
        self.settings = settings
        self.api = api
        self.demo = demo
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.camera: Camera | None = None
        self.thread: QThread | None = None
        self.worker: CaptureWorker | None = None
        self.sleep_blocker = SleepBlocker()
        self.snapshots = SnapshotStore(data_dir() / "snapshots", SecretStore())
        self.demo_devices = [{"id": "demo_phone", "kind": "phone", "name": "iPhone (SIMULADO)", "is_me": False}]
        self._last_frame: np.ndarray | None = None
        self.motion = MotionDetector(MotionSettings(
            sensitivity=settings["sensitivity"],
            cooldown_seconds=settings["cooldown_seconds"],
            zone=tuple(settings["zone"]) if settings["zone"] else None,
        ))
        self.agent: LiveAgent | None = None
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle("RoomCam" + (" · MODO DEMO" if demo else ""))
        self.resize(980, 640)
        self._build_ui()
        self._build_tray()

        self.status_changed.connect(self._on_server_state)
        self.viewers_changed.connect(self._on_viewers)
        self.motion_reported.connect(self._on_motion_reported)

        if not demo and api is not None:
            self.agent = LiveAgent(
                api, self._frame_for_live,
                on_state=lambda k, m: self.status_changed.emit(k, m),
                on_viewers=lambda n: self.viewers_changed.emit(n),
            )
            self.agent.start()
        self.refresh_cameras()
        self.refresh_devices()
        self.refresh_snapshots()
        self.snapshots.purge_older_than(settings["snapshot_retention_days"])

    # ------------------------------------------------ interfaz

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)

        if self.demo:
            banner = QLabel("MODO DEMO · El emparejamiento, los teléfonos y las notificaciones son SIMULADOS. "
                            "La cámara y la detección de movimiento son reales y locales.")
            banner.setWordWrap(True)
            banner.setStyleSheet(f"background:{AMBER};color:white;padding:8px;border-radius:6px;font-weight:bold")
            outer.addWidget(banner)

        # Indicadores siempre visibles
        ind = QHBoxLayout()
        self.monitor_chip = QLabel()
        self.camera_chip = QLabel()
        self.server_chip = QLabel()
        self.viewers_chip = QLabel()
        for w in (self.monitor_chip, self.camera_chip, self.server_chip, self.viewers_chip):
            w.setStyleSheet("padding:6px 10px;border-radius:12px;color:white;font-weight:bold")
            ind.addWidget(w)
        ind.addStretch()
        outer.addLayout(ind)

        tabs = QTabWidget()
        tabs.addTab(self._monitor_tab(), "Vigilancia")
        tabs.addTab(self._devices_tab(), "Teléfonos")
        tabs.addTab(self._privacy_tab(), "Privacidad y datos")
        outer.addWidget(tabs)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{RED};font-weight:bold")
        outer.addWidget(self.error_label)
        self.setCentralWidget(root)

        # Atajo para detener al instante
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self.stop_monitoring)
        self._set_chips(monitoring=False)
        self._on_server_state("demo" if self.demo else "connecting", "")
        self._on_viewers(0)

    def _monitor_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        self.preview = PreviewLabel()
        self.preview.zone = self.motion.settings.zone
        self.preview.zone_changed.connect(self._set_zone)
        left.addWidget(self.preview, 1)
        hint = QLabel("Arrastra sobre la imagen para limitar la detección a una zona.")
        hint.setStyleSheet(f"color:{GREY}")
        left.addWidget(hint)
        lay.addLayout(left, 3)

        right = QVBoxLayout()
        cam_row = QHBoxLayout()
        self.camera_combo = QComboBox()
        refresh = QPushButton("Buscar")
        refresh.clicked.connect(self.refresh_cameras)
        cam_row.addWidget(self.camera_combo, 1)
        cam_row.addWidget(refresh)
        right.addWidget(QLabel("<b>Cámara</b>"))
        right.addLayout(cam_row)

        self.toggle_btn = QPushButton()
        self.toggle_btn.setMinimumHeight(56)
        self.toggle_btn.clicked.connect(self.toggle_monitoring)
        right.addWidget(self.toggle_btn)
        right.addWidget(QLabel("Atajo para detener: Ctrl+Shift+S"))

        right.addWidget(QLabel("<b>Sensibilidad</b>"))
        self.sens = QSlider(Qt.Horizontal, minimum=1, maximum=100, value=self.motion.settings.sensitivity)
        self.sens_label = QLabel()
        self.sens.valueChanged.connect(self._set_sensitivity)
        right.addWidget(self.sens)
        right.addWidget(self.sens_label)
        self._set_sensitivity(self.sens.value())

        right.addWidget(QLabel("<b>Espera entre alertas</b> (evita avisos repetidos)"))
        self.cooldown = QSpinBox(minimum=10, maximum=3600, suffix=" s", value=self.motion.settings.cooldown_seconds)
        self.cooldown.valueChanged.connect(self._set_cooldown)
        right.addWidget(self.cooldown)

        zone_btn = QPushButton("Usar toda la imagen")
        zone_btn.clicked.connect(lambda: self._set_zone(None))
        right.addWidget(zone_btn)

        right.addWidget(QLabel("<b>Movimiento actual</b>"))
        self.meter = QProgressBar(maximum=100)
        self.meter.setFormat("")
        right.addWidget(self.meter)
        self.last_alert = QLabel("Sin alertas todavía.")
        self.last_alert.setWordWrap(True)
        right.addWidget(self.last_alert)

        self.prevent_sleep = QCheckBox("Evitar que la laptop se duerma mientras vigila")
        self.prevent_sleep.setChecked(self.settings["prevent_sleep"])
        self.prevent_sleep.toggled.connect(lambda v: self.settings.__setitem__("prevent_sleep", v))
        right.addWidget(self.prevent_sleep)
        right.addStretch()
        lay.addLayout(right, 2)
        return w

    def _devices_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        pair = QPushButton("Emparejar un teléfono (código o QR)")
        pair.clicked.connect(self.pair_phone)
        lay.addWidget(pair)
        lay.addWidget(QLabel("<b>Dispositivos con acceso</b>"))
        self.device_list = QListWidget()
        lay.addWidget(self.device_list, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Actualizar")
        refresh.clicked.connect(self.refresh_devices)
        revoke = QPushButton("Revocar acceso del seleccionado")
        revoke.clicked.connect(self.revoke_selected)
        row.addWidget(refresh)
        row.addWidget(revoke)
        lay.addLayout(row)
        lay.addWidget(QLabel("Revocar un teléfono le quita el acceso al instante y corta su vista en vivo."))
        return w

    def _privacy_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel(
            "<b>Qué sale de esta laptop:</b> solo la hora de cada alerta (al servidor) y el vídeo en vivo "
            "cuando tú lo abres desde el teléfono (cifrado de extremo a extremo con DTLS-SRTP). "
            "El análisis de movimiento es local. No se graba vídeo."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        snap_box = QGroupBox("Fotos instantáneas (opcional)")
        sl = QVBoxLayout(snap_box)
        self.snap_enable = QCheckBox("Guardar una foto en esta laptop cuando haya una alerta")
        self.snap_enable.setChecked(self.settings["snapshots_enabled"])
        self.snap_enable.toggled.connect(self._toggle_snapshots)
        sl.addWidget(self.snap_enable)
        ret_row = QHBoxLayout()
        ret_row.addWidget(QLabel("Borrar fotos automáticamente después de"))
        self.snap_ret = QSpinBox(minimum=1, maximum=90, suffix=" días", value=self.settings["snapshot_retention_days"])
        self.snap_ret.valueChanged.connect(lambda v: self.settings.__setitem__("snapshot_retention_days", v))
        ret_row.addWidget(self.snap_ret)
        ret_row.addStretch()
        sl.addLayout(ret_row)
        sl.addWidget(QLabel("Se guardan cifradas solo en esta laptop. Nunca se envían al servidor ni al teléfono."))
        self.snap_list = QListWidget()
        self.snap_list.itemDoubleClicked.connect(self._view_snapshot)
        sl.addWidget(self.snap_list)
        srow = QHBoxLayout()
        del_one = QPushButton("Borrar foto seleccionada")
        del_one.clicked.connect(self._delete_snapshot)
        del_all = QPushButton("Borrar todas las fotos")
        del_all.clicked.connect(self._delete_all_snapshots)
        srow.addWidget(del_one)
        srow.addWidget(del_all)
        sl.addLayout(srow)
        lay.addWidget(snap_box)

        acc = QGroupBox("Cuenta")
        al = QHBoxLayout(acc)
        clear_events = QPushButton("Borrar historial de alertas del servidor")
        clear_events.clicked.connect(self._clear_events)
        logout = QPushButton("Cerrar sesión en esta laptop")
        logout.clicked.connect(self._logout)
        delete = QPushButton("Eliminar cuenta y revocar todo")
        delete.setStyleSheet(f"color:{RED}")
        delete.clicked.connect(self._delete_account)
        for b in (clear_events, logout, delete):
            b.setEnabled(not self.demo)
            al.addWidget(b)
        lay.addWidget(acc)
        lay.addStretch()
        return w

    def _build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._dot_icon(GREY), self)
        menu = QMenu()
        menu.addAction(QAction("Mostrar RoomCam", self, triggered=self.showNormal))
        menu.addAction(QAction("Detener vigilancia y apagar cámara", self, triggered=self.stop_monitoring))
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("RoomCam: vigilancia detenida")
        self.tray.show()

    @staticmethod
    def _dot_icon(color: str) -> QIcon:
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.end()
        return QIcon(pm)

    # ------------------------------------------------ indicadores

    def _chip(self, label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"padding:6px 10px;border-radius:12px;color:white;font-weight:bold;background:{color}")

    def _set_chips(self, monitoring: bool) -> None:
        if monitoring:
            self._chip(self.monitor_chip, "● VIGILANDO", RED)
            self._chip(self.camera_chip, "CÁMARA EN USO", RED)
            self.toggle_btn.setText("Detener vigilancia y apagar cámara")
            self.toggle_btn.setStyleSheet(f"background:{RED};color:white;font-size:16px;font-weight:bold")
        else:
            self._chip(self.monitor_chip, "Vigilancia detenida", GREY)
            self._chip(self.camera_chip, "Cámara apagada", GREY)
            self.toggle_btn.setText("Iniciar vigilancia")
            self.toggle_btn.setStyleSheet(f"background:{GREEN};color:white;font-size:16px;font-weight:bold")
        if self.tray:
            self.tray.setIcon(self._dot_icon(RED if monitoring else GREY))
            self.tray.setToolTip("RoomCam: VIGILANDO (cámara en uso)" if monitoring else "RoomCam: vigilancia detenida")

    def _on_server_state(self, kind: str, msg: str) -> None:
        colors = {"online": (GREEN, "Servidor conectado"), "offline": (AMBER, "Sin conexión"),
                  "connecting": (GREY, "Conectando…"), "error": (RED, "Sesión no válida"),
                  "demo": (AMBER, "Servidor: SIMULADO")}
        color, text = colors.get(kind, (GREY, kind))
        self._chip(self.server_chip, text, color)
        if kind in ("offline", "error"):
            self.error_label.setText(msg)
        elif kind == "online" and self.error_label.text().startswith("Sin conexión"):
            self.error_label.setText("")

    def _on_viewers(self, n: int) -> None:
        if n:
            self._chip(self.viewers_chip, f"VISTA EN VIVO ABIERTA ({n})", RED)
            self.viewers_chip.show()
        else:
            self.viewers_chip.hide()

    # ------------------------------------------------ cámara y vigilancia

    def refresh_cameras(self) -> None:
        if self.camera and self.camera.is_open:
            return
        self.camera_combo.clear()
        cams = list_cameras()
        for c in cams:
            self.camera_combo.addItem(c.name, c.index)
        if not cams:
            self.camera_combo.addItem("No se encontró ninguna cámara", -1)
            self.error_label.setText("No se encontró ninguna cámara. Conecta una o revisa los permisos y pulsa «Buscar».")
        else:
            i = self.camera_combo.findData(self.settings["camera_index"])
            self.camera_combo.setCurrentIndex(max(0, i))

    def toggle_monitoring(self) -> None:
        if self.worker:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self) -> None:
        index = self.camera_combo.currentData()
        if index is None or index < 0:
            self.error_label.setText("No hay ninguna cámara disponible.")
            return
        self.error_label.setText("")
        self.settings["camera_index"] = index
        self.camera = Camera(index)
        self.thread = QThread(self)
        self.worker = CaptureWorker(self.camera, self.motion)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frame.connect(self._on_frame)
        self.worker.alert.connect(self._on_alert)
        self.worker.error.connect(self._on_camera_error)
        self.worker.stopped.connect(self._on_worker_stopped)
        self.thread.start()
        self.camera_combo.setEnabled(False)
        self._set_chips(True)
        if self.prevent_sleep.isChecked():
            self.sleep_blocker.enable()
        if self.agent:
            self.agent.set_monitoring(True, self.camera_combo.currentText())

    def stop_monitoring(self) -> None:
        if self.agent:
            self.agent.set_monitoring(False)
        if self.worker:
            self.worker.stop()
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self._on_worker_stopped()

    def _on_worker_stopped(self) -> None:
        if self.camera:
            self.camera.release()
        self.worker = None
        self.thread = None
        self._last_frame = None
        self.sleep_blocker.disable()
        self.preview.show_placeholder()
        self.meter.setValue(0)
        self.camera_combo.setEnabled(True)
        self._set_chips(False)
        if self.agent:
            self.agent.set_monitoring(False)

    def _on_camera_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        QMessageBox.warning(self, "Problema con la cámara", msg)

    def _on_frame(self, img: QImage, score: float, boxes: list) -> None:
        self.preview.set_frame(img, boxes)
        threshold = self.motion.settings.min_area_fraction()
        self.meter.setValue(min(100, int(score / max(threshold, 1e-6) * 50)))

    def _frame_for_live(self):
        cam = self.camera
        return cam.latest() if cam and cam.is_open else None

    def _on_alert(self, ts: float) -> None:
        self.last_alert.setText(f"Última alerta: {fmt_time(ts)}")
        if self.snap_enable.isChecked() and self.camera:
            frame = self.camera.latest()
            if frame is not None:
                self.snapshots.save(frame, ts)
                self.refresh_snapshots()
        if self.demo:
            msg = f"[SIMULADO] Notificación de prueba: movimiento a las {fmt_time(ts)}. No se envió a ningún teléfono."
            if self.tray:
                self.tray.showMessage("RoomCam · DEMO", msg)
            self.last_alert.setText(msg)
            return

        def send():
            try:
                r = self.api.report_motion(ts)
                if not r.get("push_enabled", True):
                    self.motion_reported.emit("Alerta guardada, pero el servidor no tiene configuradas las notificaciones (VAPID).", False)
                elif r.get("throttled"):
                    self.motion_reported.emit("Alerta guardada (el servidor limitó la notificación por ser muy seguida).", True)
                else:
                    self.motion_reported.emit(f"Notificación enviada a {r.get('notified', 0)} teléfono(s).", True)
            except NetworkError as exc:
                self.motion_reported.emit(f"No se pudo avisar al teléfono: {exc}", False)
            except ApiError as exc:
                self.motion_reported.emit(f"No se pudo avisar al teléfono: {exc}", False)

        self.pool.submit(send)

    def _on_motion_reported(self, msg: str, ok: bool) -> None:
        self.last_alert.setText(self.last_alert.text().split("\n")[0] + "\n" + msg)
        if not ok:
            self.error_label.setText(msg)

    def _set_sensitivity(self, v: int) -> None:
        self.motion.settings.sensitivity = v
        self.settings["sensitivity"] = v
        word = "baja" if v < 35 else "media" if v < 70 else "alta"
        self.sens_label.setText(f"{v} ({word})")

    def _set_cooldown(self, v: int) -> None:
        self.motion.settings.cooldown_seconds = v
        self.settings["cooldown_seconds"] = v

    def _set_zone(self, zone) -> None:
        self.motion.settings.zone = zone
        self.preview.zone = zone
        self.preview.update()
        self.settings["zone"] = list(zone) if zone else None

    # ------------------------------------------------ teléfonos

    def _run_api(self, fn, on_ok=None):
        try:
            result = fn()
        except NetworkError as exc:
            self.error_label.setText(str(exc))
            return None
        except AuthError as exc:
            self.error_label.setText(str(exc))
            return None
        except ApiError as exc:
            QMessageBox.warning(self, "RoomCam", str(exc))
            return None
        if on_ok:
            on_ok(result)
        return result

    def pair_phone(self) -> None:
        if self.demo:
            PairingDialog("DEMO-0000", "roomcam-demo://pair?code=DEMO-0000", time.time() + 300, True, self).exec()
            return
        info = self._run_api(self.api.pairing_code)
        if info:
            PairingDialog(info["code"], info["pair_url"], info["expires_at"], False, self).exec()
            self.refresh_devices()

    def refresh_devices(self) -> None:
        self.device_list.clear()
        devices = self.demo_devices if self.demo else (self._run_api(self.api.devices) or [])
        for d in devices:
            kind = "Laptop" if d["kind"] == "laptop" else "Teléfono"
            extra = " (esta laptop)" if d.get("is_me") else ""
            if d["kind"] == "phone" and not self.demo:
                extra += " · alertas " + ("activadas" if d.get("alerts_enabled") else "desactivadas")
                extra += " · conectado" if d.get("connected") else ""
            item = QListWidgetItem(f"{kind}: {d['name']}{extra}")
            item.setData(Qt.UserRole, d)
            self.device_list.addItem(item)
        if not devices:
            self.device_list.addItem("Todavía no hay teléfonos emparejados.")

    def revoke_selected(self) -> None:
        item = self.device_list.currentItem()
        d = item.data(Qt.UserRole) if item else None
        if not d:
            return
        if d.get("is_me"):
            QMessageBox.information(self, "RoomCam", "Para quitar esta laptop usa «Cerrar sesión» en Privacidad y datos.")
            return
        if QMessageBox.question(self, "Revocar acceso", f"¿Quitar el acceso a «{d['name']}»?") != QMessageBox.Yes:
            return
        if self.demo:
            self.demo_devices = [x for x in self.demo_devices if x["id"] != d["id"]]
        else:
            self._run_api(lambda: self.api.revoke(d["id"]))
        self.refresh_devices()

    # ------------------------------------------------ privacidad

    def _toggle_snapshots(self, on: bool) -> None:
        self.settings["snapshots_enabled"] = on

    def refresh_snapshots(self) -> None:
        self.snap_list.clear()
        for s in self.snapshots.list():
            item = QListWidgetItem(f"Foto del {fmt_time(s.ts)}")
            item.setData(Qt.UserRole, s)
            self.snap_list.addItem(item)

    def _view_snapshot(self, item) -> None:
        s = item.data(Qt.UserRole)
        try:
            frame = self.snapshots.load(s)
        except RuntimeError as exc:
            QMessageBox.warning(self, "RoomCam", str(exc))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(item.text())
        l = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(to_qimage(frame)).scaledToWidth(640))
        l.addWidget(lbl)
        dlg.exec()

    def _delete_snapshot(self) -> None:
        item = self.snap_list.currentItem()
        if item:
            self.snapshots.delete(item.data(Qt.UserRole))
            self.refresh_snapshots()

    def _delete_all_snapshots(self) -> None:
        if QMessageBox.question(self, "Borrar fotos", "¿Borrar todas las fotos guardadas en esta laptop?") == QMessageBox.Yes:
            n = self.snapshots.delete_all()
            self.refresh_snapshots()
            QMessageBox.information(self, "RoomCam", f"Se borraron {n} foto(s).")

    def _clear_events(self) -> None:
        if self._run_api(lambda: self.api._request("DELETE", "/api/events")) is not None:
            QMessageBox.information(self, "RoomCam", "Historial de alertas borrado del servidor.")

    def _logout(self) -> None:
        if QMessageBox.question(self, "Cerrar sesión", "Esto apaga la cámara y revoca esta laptop. ¿Continuar?") != QMessageBox.Yes:
            return
        self.stop_monitoring()
        if self.agent:
            self.agent.stop()
        self.api.logout()
        QMessageBox.information(self, "RoomCam", "Sesión cerrada. Vuelve a abrir RoomCam para iniciar sesión.")
        self.close()

    def _delete_account(self) -> None:
        pw, ok = QInputDialog.getText(
            self, "Eliminar cuenta",
            "Esto borra tu cuenta, revoca todos tus dispositivos y borra el historial del servidor.\n"
            "No se puede deshacer. Escribe tu contraseña para confirmar:", QLineEdit.Password,
        )
        if not ok or not pw:
            return
        self.stop_monitoring()
        if self._run_api(lambda: self.api.delete_account(pw)) is None:
            return
        n = self.snapshots.delete_all()
        if self.agent:
            self.agent.stop()
        QMessageBox.information(self, "RoomCam", f"Cuenta eliminada. También se borraron {n} foto(s) locales.")
        self.close()

    # ------------------------------------------------ cierre

    def closeEvent(self, e) -> None:
        # Cerrar la ventana siempre detiene la vigilancia y libera la cámara.
        self.stop_monitoring()
        if self.agent:
            self.agent.stop()
        if self.tray:
            self.tray.hide()
        self.pool.shutdown(wait=False)
        e.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RoomCam")
    settings = Settings()
    demo = "--demo" in sys.argv
    api = None
    if not demo:
        store = SecretStore()
        saved = store.get("server")
        if saved and store.get("refresh_token"):
            api = ApiClient(saved, store)
        else:
            dlg = SetupDialog(settings)
            if dlg.exec() != QDialog.Accepted:
                return 0
            demo, api = dlg.demo, dlg.api
    win = MainWindow(settings, api, demo)
    win.show()
    return app.exec()
