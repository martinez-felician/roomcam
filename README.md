# RoomCam

Cámara de seguridad privada para tu laptop, con alertas y vista en vivo en tu teléfono.

- La laptop detecta movimiento **localmente**. No graba vídeo por defecto.
- El teléfono recibe una notificación con la hora del evento y puede abrir una **vista en vivo cifrada** (WebRTC, DTLS-SRTP).
- No hay puertos abiertos ni URL pública de la cámara. Cuentas autenticadas, emparejamiento con código de un solo uso y credenciales por dispositivo que se pueden revocar.

| Carpeta | Contenido |
|---|---|
| [`server/`](server/) | Servidor Python (FastAPI): cuentas, emparejamiento, señalización, Web Push |
| [`laptop/`](laptop/) | App de escritorio Python (PySide6, OpenCV, aiortc) para Windows, macOS y Linux |
| [`phone/`](phone/) | App web instalable (PWA) para iPhone y Android |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Diseño, seguridad y qué datos salen de la laptop |
| [`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md) | Credenciales, despliegue y puesta en marcha paso a paso |

## Empezar

1. **Probar sin configurar nada:** `python -m roomcam_laptop --demo` en la laptop ([instrucciones](laptop/README.md)).
2. **Sistema real gratis:** servidor en tu propia laptop con Tailscale, ver [docs/SERVIDOR-EN-TU-LAPTOP.md](docs/SERVIDOR-EN-TU-LAPTOP.md) (en Windows basta con `iniciar-servidor.ps1`).
3. **Servidor siempre encendido en internet:** sigue [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) (Fly.io).

## Estado

### Funciona de extremo a extremo (probado automáticamente)

- Cuentas, inicio de sesión con límite de intentos, renovación y revocación de credenciales.
- Emparejamiento con código/QR que caduca a los 5 minutos y solo sirve una vez.
- Detección de movimiento con sensibilidad, espera entre alertas, confirmación de varios fotogramas y zona de detección.
- Registro de alertas y envío de notificación mínima (hora, nombre, enlace).
- Vista en vivo WebRTC laptop → teléfono, probada con el servidor real, el agente de la laptop y la app del teléfono en Chromium. Se rechaza si la vigilancia está detenida y se corta al detenerla.
- Estado en el teléfono: vigilando, detenida, dormida, desconectada.
- Revocar dispositivos, borrar historial, fotos opcionales cifradas con borrado individual y total, eliminar la cuenta.
- Modo demo en la laptop y en el teléfono, con todo lo simulado marcado como SIMULADO.

Pruebas: `server/` 13, `laptop/` 9 (incluye una de extremo a extremo con vídeo) y `laptop/tests/browser_e2e.py` (navegador real).

### Necesita credenciales o despliegue

- **Servidor en internet con https** (Fly.io u otro). Sin esto el teléfono no puede conectarse fuera de tu red.
- **Claves VAPID** para que lleguen las notificaciones (se generan gratis con `python -m roomcam_server.keys`).
- **TURN** para que el vídeo funcione en redes móviles o con NAT estricto.
- No hace falta cuenta de Apple Developer ni Firebase.

### No probado todavía

- Cámara física real (las pruebas usan fotogramas sintéticos).
- Notificación push real en un iPhone (requiere el servidor desplegado).

### Limitaciones de plataforma

- **iPhone:** notificaciones solo con iOS 16.4+ y la app añadida a la pantalla de inicio. Sin botones de acción en la notificación (al tocarla se abre la vista en vivo). El vídeo solo se ve con la app abierta.
- **Laptop:** vigila mientras la app está abierta. Si se suspende o se apaga deja de vigilar; el teléfono lo muestra como «dormida» o «desconectada». Opción para evitar la suspensión mientras vigila.
- **macOS/Linux:** soportados por el mismo código, pero solo se han probado partes sin cámara en Linux.
