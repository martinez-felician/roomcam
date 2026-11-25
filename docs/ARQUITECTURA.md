# RoomCam: arquitectura

Cámara de seguridad privada: la laptop detecta movimiento con su cámara, avisa al iPhone emparejado y, desde el iPhone, se puede abrir una vista en vivo cifrada.

## Componentes

```
┌──────────────────────┐        HTTPS / WSS (salientes)        ┌─────────────────────┐
│  Laptop (Python)     │ ───────────────────────────────────▶ │  Servidor (FastAPI) │
│  - OpenCV: cámara    │                                       │  - Cuentas          │
│  - Detección local   │ ◀─────── señalización WebRTC ───────▶ │  - Emparejamiento   │
│  - aiortc: WebRTC    │                                       │  - Señalización WSS │
│  - PySide6: ventana  │                                       │  - Web Push (VAPID) │
└─────────┬────────────┘                                       │  - SQLite           │
          │                                                    └─────────┬───────────┘
          │   vídeo WebRTC cifrado (DTLS-SRTP), P2P o vía TURN           │ HTTPS / WSS
          ▼                                                              ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  iPhone: app web instalable (PWA) en Safari, añadida a la pantalla de inicio     │
│  - Notificaciones Web Push (iOS 16.4 o superior)                                 │
│  - Vista en vivo WebRTC, estado de la laptop, dispositivos, revocar acceso       │
└──────────────────────────────────────────────────────────────────────────────────┘
```

| Parte | Tecnología | Por qué |
|---|---|---|
| Laptop | Python 3.11+, OpenCV, aiortc, PySide6 | Python como pediste; OpenCV es estándar para cámara y movimiento; aiortc implementa WebRTC con DTLS-SRTP; PySide6 funciona en Windows, macOS y Linux |
| Servidor | Python, FastAPI, WebSockets, SQLite (luego Postgres si hace falta), pywebpush | Mismo lenguaje en todo el proyecto, ligero y fácil de desplegar |
| Teléfono | PWA (HTML + JavaScript) | Funciona en iPhone y Android sin tiendas ni cuenta de Apple Developer; Safari soporta WebRTC y Web Push |
| Red | STUN público + TURN (Cloudflare Calls, Twilio o coturn propio) | Atraviesa NAT y redes móviles sin abrir puertos |

## Reglas de seguridad

- **Sin puertos de entrada:** la laptop solo abre conexiones salientes al servidor. No hay URL pública de la cámara.
- **Cuentas autenticadas:** correo + contraseña (hash Argon2) en el servidor.
- **Emparejamiento:** la laptop pide un código de 8 caracteres y un QR. Caduca a los 5 minutos y solo se puede usar una vez.
- **Credenciales por dispositivo:** cada dispositivo recibe un token de acceso de 15 minutos y un token de renovación propio. Revocar un dispositivo borra su token de renovación y cierra sus conexiones en el acto.
- **Vídeo:** WebRTC cifra el vídeo con DTLS-SRTP. El servidor solo intercambia mensajes de señalización y nunca ve el vídeo. TURN, si se usa, solo reenvía paquetes cifrados.
- **Credenciales TURN temporales:** se generan por sesión y caducan a la hora.
- **Notificaciones mínimas:** el aviso contiene solo la hora del evento y un enlace a la app, que pide sesión iniciada. Nunca imágenes.

## Qué datos salen de la laptop

| Dato | A dónde va | Cuánto tiempo se guarda |
|---|---|---|
| Correo y hash de contraseña | Servidor | Hasta que borres la cuenta |
| Lista de dispositivos emparejados | Servidor | Hasta que los revoques o borres la cuenta |
| Evento de movimiento (hora, sin imagen) | Servidor y servicio de push de Apple/Google | Servidor: 30 días. Push: lo que tarde en entregarse |
| Mensajes de señalización WebRTC | Servidor (en memoria) | Solo mientras dura la conexión |
| Vídeo en vivo | Directo al teléfono (o vía TURN, cifrado) | No se guarda |
| Fotos instantáneas (opcional, apagado por defecto) | Solo en la laptop, cifradas | Hasta que las borres; borrado individual o total |

El análisis de movimiento y las imágenes de la cámara nunca salen de la laptop salvo que abras la vista en vivo.

## Limitaciones conocidas

- **iPhone:** las notificaciones de la PWA requieren iOS 16.4 o superior y que la app esté añadida a la pantalla de inicio. Si se quisiera una app nativa haría falta una cuenta de Apple Developer (99 USD al año).
- **Laptop dormida:** si la laptop se suspende, deja de vigilar. La app evita la suspensión mientras vigila (solo si lo permites) y el teléfono muestra "laptop desconectada" si deja de recibir señal.
- **Cámara en segundo plano:** la app vigila solo mientras está abierta y muestra siempre un indicador. Al detener, la cámara se libera de inmediato.

## Estructura del repositorio

```
roomcam/
├── docs/      Arquitectura, privacidad y guías de instalación
├── server/    Servidor FastAPI
├── laptop/    App de escritorio en Python
└── phone/     PWA para iPhone y Android
```
