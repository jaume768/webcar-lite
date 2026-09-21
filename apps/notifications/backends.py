"""Backend de correo de Django que envia por la API transaccional de Brevo.

Con `BREVO_API_KEY` puesto, `send_mail` y compania salen por Brevo: HTML,
texto y adjuntos. Sin clave, cada entorno usa el suyo (consola en desarrollo,
SMTP en produccion; Brevo tambien sirve por SMTP con smtp-relay.brevo.com).
"""

import base64
from email.utils import parseaddr

import structlog
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from apps.core.http import HttpError, post_json

logger = structlog.get_logger(__name__)

API = "https://api.brevo.com/v3/smtp/email"


def _contacto(direccion: str) -> dict:
    nombre, correo = parseaddr(direccion)
    return {"email": correo, "name": nombre} if nombre else {"email": correo}


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages) -> int:
        enviados = 0
        for mensaje in email_messages:
            try:
                self._enviar(mensaje)
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("brevo_envio_fallido", asunto=mensaje.subject)
            else:
                enviados += 1
        return enviados

    def _enviar(self, mensaje) -> None:
        cuerpo = {
            "sender": _contacto(mensaje.from_email or settings.DEFAULT_FROM_EMAIL),
            "to": [_contacto(destino) for destino in mensaje.to],
            "subject": mensaje.subject,
            "textContent": mensaje.body,
        }
        if mensaje.cc:
            cuerpo["cc"] = [_contacto(d) for d in mensaje.cc]
        if mensaje.bcc:
            cuerpo["bcc"] = [_contacto(d) for d in mensaje.bcc]
        if mensaje.reply_to:
            cuerpo["replyTo"] = _contacto(mensaje.reply_to[0])
        for contenido, tipo in getattr(mensaje, "alternatives", []):
            if tipo == "text/html":
                cuerpo["htmlContent"] = contenido
        adjuntos = []
        for adjunto in mensaje.attachments:
            nombre, datos = adjunto[0], adjunto[1]
            if isinstance(datos, str):
                datos = datos.encode()
            adjuntos.append({"name": nombre, "content": base64.b64encode(datos).decode()})
        if adjuntos:
            cuerpo["attachment"] = adjuntos

        try:
            respuesta = post_json(API, cuerpo, headers={"api-key": settings.BREVO_API_KEY})
        except HttpError as exc:
            raise ConnectionError(f"Sin conexion con Brevo: {exc}") from exc
        if not respuesta.ok:
            raise ConnectionError(f"Brevo HTTP {respuesta.status}: {respuesta.body[:500]}")
        # Brevo devuelve el id del mensaje: se guarda para rastrearlo.
        mensaje.extra_headers["X-Brevo-Message-Id"] = (respuesta.json() or {}).get("messageId", "")
