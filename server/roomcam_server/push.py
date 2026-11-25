"""Envío de notificaciones Web Push (VAPID). El contenido es mínimo: tipo, hora y enlace."""
from __future__ import annotations

import json
import logging

log = logging.getLogger("roomcam.push")


class PushSender:
    def __init__(self, settings) -> None:
        self.settings = settings

    def send(self, subscription: dict, payload: dict) -> str:
        """Devuelve 'sent', 'gone' (suscripción caducada, hay que borrarla), 'disabled' o 'error'."""
        if not self.settings.push_enabled:
            log.info("Web Push no configurado; notificación no enviada")
            return "disabled"
        from pywebpush import webpush, WebPushException

        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload),
                vapid_private_key=self.settings.vapid_private_key,
                vapid_claims={"sub": self.settings.vapid_subject},
                ttl=600,
            )
            return "sent"
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                return "gone"
            log.warning("Fallo al enviar Web Push: %s", exc)
            return "error"
