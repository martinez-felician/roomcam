# Puesta en marcha de RoomCam

Tres partes: **servidor** (en internet, con https), **laptop** (tu HP) y **teléfono** (tu iPhone). Calcula entre 30 y 60 minutos la primera vez.

## 0. Probar sin nada configurado (modo demo)

- **Laptop:** `python -m roomcam_laptop --demo` (ver [laptop/README.md](../laptop/README.md)). Usa tu cámara real; teléfono y notificaciones son simulados.
- **Teléfono:** abre la app servida por cualquier servidor RoomCam y pulsa «Probar modo demo». Laptop, alertas y vídeo son simulados y están marcados como tal.

## 1. Credenciales que necesitas

| Qué | Para qué | Cómo conseguirlo | Coste |
|---|---|---|---|
| Hosting con https | Servidor y app del teléfono | Fly.io (recomendado, abajo), Railway, un VPS, o tu propio PC con Cloudflare Tunnel | Fly.io: unos pocos USD al mes |
| `ROOMCAM_JWT_SECRET` | Firmar las sesiones | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Gratis |
| Claves VAPID | Notificaciones push (iPhone y Android) | `python -m roomcam_server.keys` dentro de `server/` | Gratis |
| TURN | Vídeo en redes móviles o con NAT estricto | Metered.ca (tiene plan gratuito, usuario y contraseña fijos) o coturn propio (secreto compartido) | Gratis a pocos USD |
| Cuenta Apple Developer | **No hace falta.** La app del iPhone es web instalable | — | 0 |
| Firebase / FCM | **No hace falta.** Web Push usa los servicios de Apple y Google automáticamente | — | 0 |

## 2. Desplegar el servidor en Fly.io

1. Crea una cuenta en <https://fly.io> e instala `flyctl` (en Windows, en PowerShell: `iwr https://fly.io/install.ps1 -useb | iex`).
2. En la carpeta raíz del proyecto:

   ```bash
   fly auth login
   # Edita fly.toml y cambia "roomcam-CAMBIA-ESTE-NOMBRE" por un nombre único, p. ej. roomcam-fabian
   fly launch --no-deploy --copy-config
   fly volumes create roomcam_data --size 1
   ```

3. Genera las claves y guárdalas como secretos (nunca en GitHub):

   ```bash
   cd server && pip install -r requirements.txt && python -m roomcam_server.keys && cd ..
   fly secrets set \
     ROOMCAM_JWT_SECRET="(el valor generado)" \
     ROOMCAM_PUBLIC_URL="https://roomcam-fabian.fly.dev" \
     ROOMCAM_VAPID_PUBLIC_KEY="(clave pública)" \
     ROOMCAM_VAPID_PRIVATE_KEY="(clave privada)" \
     ROOMCAM_VAPID_SUBJECT="mailto:tu-correo@ejemplo.com"
   ```

4. Si tienes TURN, añade también:
   - Metered u otro con usuario fijo: `ROOMCAM_TURN_URLS="turn:a.relay.metered.ca:443?transport=tcp,turn:a.relay.metered.ca:80"`, `ROOMCAM_TURN_USERNAME`, `ROOMCAM_TURN_CREDENTIAL`.
   - coturn con `use-auth-secret`: `ROOMCAM_TURN_URLS` y `ROOMCAM_TURN_SECRET` (las credenciales que reciben los dispositivos caducan en 1 hora).
5. `fly deploy`. Comprueba <https://roomcam-fabian.fly.dev/api/health> → `{"ok": true}`.

### Alternativa sin hosting: tu propio PC + Cloudflare Tunnel

Ejecuta el servidor en un PC siempre encendido (`uvicorn --factory roomcam_server.main:create_app`) y publica `http://localhost:8000` con `cloudflared tunnel` para obtener una URL https. No abre puertos de entrada. Ese PC debe estar encendido para que lleguen las alertas.

## 3. Laptop

1. Instala la app (ver [laptop/README.md](../laptop/README.md)).
2. `python -m roomcam_laptop` → «Conectar a tu servidor» → escribe `https://roomcam-fabian.fly.dev`, tu correo y una contraseña de 10 caracteres o más → «Crear cuenta».

## 4. iPhone

Requisito: iOS 16.4 o superior (Ajustes > General > Información).

1. En la laptop: pestaña **Teléfonos > Emparejar un teléfono**.
2. En el iPhone, escanea el QR con la cámara (se abre Safari) o entra en `https://roomcam-fabian.fly.dev/app/`.
3. En Safari toca **Compartir > Añadir a pantalla de inicio**.
4. Abre **RoomCam desde el icono nuevo** y escribe el código. (En iPhone, la app instalada y Safari guardan su sesión por separado, por eso el código se escribe dentro de la app instalada.)
5. Activa **Alertas de movimiento** y acepta el permiso de notificaciones.
6. Prueba: inicia la vigilancia en la laptop, muévete delante de la cámara y espera la notificación. Tócala para abrir la vista en vivo.

En Android funciona igual desde Chrome («Instalar app»).

## 5. Qué datos se envían y cuánto se guardan

Ver la tabla en [ARQUITECTURA.md](ARQUITECTURA.md#qué-datos-salen-de-la-laptop). En resumen: el servidor guarda tu correo, el hash de la contraseña, la lista de dispositivos y la hora de las alertas (30 días). Apple/Google entregan la notificación (hora + nombre de la laptop + enlace). El vídeo va cifrado directo al teléfono y nunca se guarda.

## 6. Quitar dispositivos o eliminar la cuenta

- **Revocar un teléfono:** desde la laptop (Teléfonos > Revocar) o desde el propio teléfono (Desemparejar). Pierde el acceso al instante.
- **Revocar la laptop:** Privacidad y datos > Cerrar sesión en esta laptop.
- **Eliminar todo:** en la laptop, Privacidad y datos > **Eliminar cuenta y revocar todo**. Borra la cuenta, todos los dispositivos, el historial del servidor y las fotos locales. No se puede deshacer.
- **Borrar el servidor entero:** `fly apps destroy roomcam-fabian` borra la máquina y el volumen con la base de datos.

## 7. Solución de problemas

| Síntoma | Causa probable |
|---|---|
| La laptop dice «Sin conexión» | URL del servidor mal escrita o sin internet |
| El interruptor de alertas falla en iPhone | La app no se abrió desde la pantalla de inicio, iOS < 16.4, o faltan las claves VAPID |
| «La vigilancia está detenida» al ver en vivo | Inicia la vigilancia en la laptop |
| El vídeo no conecta fuera de casa | Falta TURN (paso 2.4) |
| El teléfono muestra «Desconectada» | La laptop está apagada, dormida o sin internet |
