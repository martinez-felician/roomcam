# Imagen del servidor RoomCam (incluye la app del teléfono en /app).
FROM python:3.12-slim

WORKDIR /srv
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
COPY phone/ phone/

ENV ROOMCAM_DB_PATH=/data/roomcam.db \
    ROOMCAM_PHONE_DIR=/srv/phone \
    PYTHONUNBUFFERED=1
WORKDIR /srv/server
EXPOSE 8000
CMD ["uvicorn", "--factory", "roomcam_server.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
