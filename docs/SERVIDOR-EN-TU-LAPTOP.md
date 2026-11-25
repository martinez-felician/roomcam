# Servidor gratis en tu propia laptop (con Tailscale)

La forma más sencilla y gratuita de usar RoomCam: el servidor corre en la misma laptop que vigila, y **Tailscale** crea una red privada entre la laptop y tu iPhone. No hay que pagar hosting ni abrir puertos, y nadie fuera de tu cuenta de Tailscale puede abrir la dirección.

**Requisito:** la laptop tiene que estar encendida y con internet (igual que para vigilar).

## 1. Instalar Tailscale (una vez)

1. En la laptop: descarga Tailscale de <https://tailscale.com/download/windows>, instálalo e inicia sesión (puedes usar tu cuenta de Google, Microsoft o Apple).
2. En el iPhone: instala **Tailscale** desde la App Store e inicia sesión con **la misma cuenta**.
3. Activa HTTPS: entra en <https://login.tailscale.com/admin/dns>, comprueba que **MagicDNS** está activado y pulsa **Enable HTTPS**.

## 2. Arrancar el servidor

1. Instala Python 3.11 o superior (marca «Add python.exe to PATH»).
2. Descarga el proyecto de GitHub (Code > Download ZIP) y descomprímelo.
3. En la carpeta del proyecto, clic derecho en `iniciar-servidor.ps1` > **Ejecutar con PowerShell**.
   - Si Windows bloquea el script, abre PowerShell en esa carpeta y ejecuta primero:
     `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
4. La primera vez instala lo necesario y crea `server/.env` con tus claves privadas. Al final verás algo así:

   ```
   En la app de la laptop usa:  http://localhost:8000
   En el iPhone abre:           https://tu-laptop.tu-red.ts.net/app/
   ```

Deja esa ventana abierta mientras uses RoomCam.

## 3. Conectar la app de la laptop

En otra ventana de PowerShell, en la carpeta `laptop`, ejecuta `python -m roomcam_laptop` (ver [laptop/README.md](../laptop/README.md)), elige «Conectar a tu servidor», escribe `http://localhost:8000` y crea tu cuenta.

## 4. Conectar el iPhone

1. Comprueba que Tailscale está **conectado** en el iPhone.
2. En la app de la laptop: **Teléfonos > Emparejar un teléfono**.
3. Escanea el QR con la cámara del iPhone (o abre en Safari la dirección `https://…ts.net/app/`).
4. En Safari: **Compartir > Añadir a pantalla de inicio**, abre RoomCam desde el icono y escribe el código.
5. Activa **Alertas de movimiento**.

## Qué tener en cuenta

- **Notificaciones:** llegan aunque Tailscale esté desconectado en el iPhone, porque las entrega Apple.
- **Vista en vivo y estado de la laptop:** necesitan Tailscale conectado en el iPhone.
- Si la laptop se apaga o se duerme, el iPhone no podrá conectarse y no habrá alertas.
- **Tus claves** están en `server/.env`. No lo compartas ni lo subas a GitHub (ya está excluido). Si lo borras, se crean claves nuevas y tendrás que volver a activar las alertas en el iPhone.
- **Uso personal:** el plan gratis de Tailscale es para uso personal. Si algún día usas RoomCam como negocio, revisa sus planes.

## Pasar después a un servidor en internet

El código es el mismo. Sigue [DESPLIEGUE.md](DESPLIEGUE.md) para Fly.io, y en la app de la laptop vuelve a iniciar sesión con la nueva dirección.
