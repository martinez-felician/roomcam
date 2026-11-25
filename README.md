# RoomCam

Cámara de seguridad privada para tu laptop, con avisos y vista en vivo en tu teléfono.

- La laptop detecta movimiento localmente y nunca graba vídeo por defecto.
- El teléfono recibe una notificación con la hora del evento y puede abrir una vista en vivo cifrada (WebRTC).
- No hay puertos abiertos ni URL pública de la cámara.

Estado: en construcción. Consulta [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) para el diseño.

| Carpeta | Contenido | Estado |
|---|---|---|
| `server/` | Servidor FastAPI | Listo (13 pruebas automáticas) |
| `laptop/` | App de escritorio (Python) | Lista (9 pruebas, incluida una de extremo a extremo con vídeo WebRTC) |
| `phone/` | App web instalable (PWA) | Pendiente |
