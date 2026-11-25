# Servidor RoomCam

Servidor en Python (FastAPI) que se encarga de:

- **Cuentas:** registro e inicio de sesión con contraseña (hash Argon2) y límite de intentos.
- **Emparejamiento:** la laptop genera un código de 8 caracteres que caduca a los 5 minutos y solo se puede usar una vez.
- **Credenciales por dispositivo:** token de acceso de 15 minutos y token de renovación que rota en cada uso. Revocar un dispositivo lo desconecta al instante.
- **Señalización WebRTC:** WebSocket autenticado que reenvía ofertas, respuestas y candidatos ICE solo entre dispositivos de la misma cuenta. El vídeo nunca pasa por aquí.
- **Estado de la laptop:** conectada, vigilando, detenida, dormida o desconectada.
- **Notificaciones Web Push:** solo tipo, hora, nombre de la laptop y enlace. Sin imágenes.
- **Credenciales TURN temporales:** compatibles con coturn (`use-auth-secret`).
- **Borrado:** eventos (uno a uno o todos), dispositivos y la cuenta completa.

## Probarlo en tu computadora

```bash
cd server
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                       # pruebas automáticas
uvicorn --factory roomcam_server.main:create_app --reload
```

Abre <http://localhost:8000/docs> para ver y probar la API.

## Variables de entorno

Consulta [.env.example](.env.example). En producción son obligatorias `ROOMCAM_JWT_SECRET`, `ROOMCAM_PUBLIC_URL` (con https) y las claves VAPID. TURN es muy recomendable.

## API resumida

| Método | Ruta | Quién | Para qué |
|---|---|---|---|
| POST | `/api/auth/register` | Cualquiera | Crear cuenta |
| POST | `/api/auth/login` | Laptop | Iniciar sesión y registrar la laptop |
| POST | `/api/auth/refresh` | Dispositivo | Renovar tokens |
| POST | `/api/pairing/codes` | Laptop | Generar código o QR de emparejamiento |
| POST | `/api/pairing/claim` | Teléfono | Canjear el código |
| GET | `/api/devices` | Dispositivo | Lista de dispositivos y estado |
| DELETE | `/api/devices/{id}` | Dispositivo | Revocar un dispositivo |
| PATCH | `/api/devices/me` | Teléfono | Activar o desactivar alertas |
| PUT/DELETE | `/api/devices/me/push` | Teléfono | Registrar o quitar notificaciones |
| POST | `/api/events/motion` | Laptop | Avisar de movimiento |
| GET/DELETE | `/api/events` | Dispositivo | Ver o borrar el historial de eventos |
| GET | `/api/ice` | Dispositivo | Servidores STUN/TURN con credenciales temporales |
| DELETE | `/api/account` | Laptop | Borrar la cuenta y todo lo asociado (pide contraseña) |
| WS | `/ws` | Dispositivo | Señalización y estado en tiempo real |
