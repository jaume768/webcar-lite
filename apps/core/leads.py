"""Alta de contactos de la portada y aviso al comercial.

La portada es publica: cualquiera puede enviar el formulario. Aqui se decide
que se guarda y a quien se avisa. La regla es una sola: el contacto se guarda
siempre; el correo de aviso es un extra y, si falla, se anota en el log y se
sigue. Un buzon mal configurado no puede costar un cliente.
"""

import structlog
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils.translation import gettext as _

from .models import Lead

logger = structlog.get_logger(__name__)


def whatsapp_link(numero: str | None = None) -> str:
    """Enlace wa.me a partir del numero dado, o del configurado si no se da.

    Se acepta escrito como se quiera (espacios, guiones, +34): wa.me solo
    admite digitos. Sin numero no hay enlace, y la portada no pinta el boton.
    """
    crudo = settings.CONTACT_WHATSAPP if numero is None else numero
    digitos = "".join(c for c in crudo if c.isdigit())
    if not digitos:
        return ""
    # Un numero espanol sin prefijo internacional se completa: wa.me lo exige.
    if len(digitos) == 9:
        digitos = "34" + digitos
    return f"https://wa.me/{digitos}"


@transaction.atomic
def register_lead(
    *,
    name: str,
    company: str = "",
    phone: str = "",
    email: str = "",
    fleet_size: int | None = None,
    message: str = "",
) -> Lead:
    """Guarda el contacto y, si hay buzon configurado, avisa por correo."""
    lead = Lead(
        name=name.strip(),
        company=company.strip(),
        phone=phone.strip(),
        email=email.strip(),
        fleet_size=fleet_size,
        message=message.strip(),
    )
    lead.full_clean()
    lead.save()

    # Se avisa despues de confirmar: nada de correos sobre filas que no existen.
    transaction.on_commit(lambda: notify_lead(lead))

    logger.info(
        "lead_recibido",
        lead_id=lead.pk,
        empresa=lead.company,
        con_telefono=bool(lead.phone),
        con_correo=bool(lead.email),
    )
    return lead


def notify_lead(lead: Lead) -> bool:
    """Manda el aviso al buzon comercial. Devuelve si se llego a enviar."""
    destino = settings.LEADS_NOTIFY_EMAIL
    if not destino:
        # Todavia no hay buzon: el contacto esta guardado y se ve en la
        # pantalla de contactos. No es un error.
        logger.info("lead_sin_aviso", lead_id=lead.pk, motivo="LEADS_NOTIFY_EMAIL sin configurar")
        return False

    cuerpo = "\n".join(
        parte
        for parte in [
            _("Nuevo contacto desde la web de RentFlow."),
            "",
            f"{_('Nombre')}: {lead.name}",
            f"{_('Empresa')}: {lead.company or '—'}",
            f"{_('Telefono')}: {lead.phone or '—'}",
            f"{_('Correo')}: {lead.email or '—'}",
            f"{_('Vehiculos')}: {lead.fleet_size if lead.fleet_size is not None else '—'}",
            "",
            lead.message or _("(sin mensaje)"),
        ]
    )
    asunto = _("Contacto web: %(empresa)s") % {"empresa": lead.company or lead.name}

    try:
        send_mail(
            asunto,
            cuerpo,
            settings.DEFAULT_FROM_EMAIL,
            [destino],
            fail_silently=False,
        )
    except Exception as exc:  # el contacto ya esta guardado: esto no se reintenta
        logger.error("lead_aviso_fallido", lead_id=lead.pk, error=f"{type(exc).__name__}: {exc}")
        return False

    logger.info("lead_avisado", lead_id=lead.pk, destino=destino)
    return True
