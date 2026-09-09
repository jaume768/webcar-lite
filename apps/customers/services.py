"""Reglas de clientes. Las vistas orquestan, aqui se decide."""

import structlog
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import Customer, CustomerDocument

logger = structlog.get_logger(__name__)


class CustomerServiceError(ServiceError):
    """Regla de negocio incumplida. La vista la convierte en aviso, no en 500."""


@transaction.atomic
def save_customer(*, customer: Customer, actor=None, office=None) -> Customer:
    """Crea o actualiza un cliente ya validado por su formulario."""
    creando = customer.pk is None
    if creando and customer.office_id is None and office is not None:
        # Oficina de alta: se toma de la oficina activa, no se pide en el
        # formulario. Es un dato de trazabilidad, no una decision del usuario.
        customer.office = office
    customer.save()
    logger.info(
        "cliente_creado" if creando else "cliente_actualizado",
        customer_id=customer.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return customer


@transaction.atomic
def set_customer_active(*, customer: Customer, active: bool, actor=None) -> Customer:
    """Da de baja o reactiva un cliente. Nunca borra: tiene historico detras."""
    if active:
        customer.activate()
    else:
        customer.deactivate()
    logger.info(
        "cliente_reactivado" if active else "cliente_desactivado",
        customer_id=customer.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return customer


@transaction.atomic
def set_blacklisted(*, customer: Customer, blacklisted: bool, reason: str = "", actor=None):
    """Marca o quita la marca de cliente conflictivo.

    Marcar exige motivo: un aviso sin explicacion no sirve de nada a quien lo
    lee tres meses despues en otro mostrador.
    """
    if blacklisted and not reason.strip():
        raise CustomerServiceError(_("Para marcar a un cliente hay que explicar el motivo."))

    customer.is_blacklisted = blacklisted
    customer.blacklist_reason = reason.strip() if blacklisted else ""
    customer.save(update_fields=["is_blacklisted", "blacklist_reason"])
    logger.info(
        "cliente_marcado" if blacklisted else "cliente_desmarcado",
        customer_id=customer.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return customer


@transaction.atomic
def save_document(*, document: CustomerDocument, actor=None) -> CustomerDocument:
    """Adjunta un documento escaneado a la ficha del cliente."""
    document.original_name = document.original_name or document.file.name
    document.save()
    logger.info(
        "documento_de_cliente_subido",
        customer_id=document.customer_id,
        document_id=document.pk,
        kind=document.kind,
        actor_id=getattr(actor, "pk", None),
    )
    return document


@transaction.atomic
def delete_document(*, document: CustomerDocument, actor=None) -> None:
    """Quita un documento y borra el fichero.

    Se borra de verdad a proposito: es dato personal, y guardarlo "por si
    acaso" despues de que alguien haya pedido quitarlo no tiene defensa.
    """
    datos = {"customer_id": document.customer_id, "document_id": document.pk}
    document.delete()
    logger.info("documento_de_cliente_borrado", **datos, actor_id=getattr(actor, "pk", None))
