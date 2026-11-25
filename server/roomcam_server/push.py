"""Envío de notificaciones Web Push (VAPID). El contenido es mínimo: tipo, hora y enlace."""
from __future__ import annotations

import json
import logging

log = logging.getLogger("roomcam.push")


class PushSender:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._session = None  # conexión reutilizada con Apple/Google: ahorra el saludo TLS en cada alerta

    def send(self, subscription: dict, payload: dict) -> str:
        """Devuelve 'sent', 'gone' (suscripción caducada, hay que borrarla), 'disabled' o 'error'."""
        if not self.settings.push_enabled:
            log.info("Web Push no configurado; notificación no enviada")
            return "disabled"
        from pywebpush import webpush, WebPushException

        if self._session is None:
            import requests
            self._session = requests.Session()
        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload),
                vapid_private_key=self.settings.vapid_private_key,
                vapid_claims={"sub": self.settings.vapid_subject},
                ttl=600,
                # "high" pide a Apple/Google entregarla al momento, aunque el teléfono
                # esté en reposo. Sin esto pueden retrasarla para ahorrar batería.
                headers={"Urgency": "high"},
                timeout=10,
                requests_session=self._session,
            )
            return "sent"
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                return "gone"
            log.warning("Fallo al enviar Web Push: %s", exc)
            return "error"
