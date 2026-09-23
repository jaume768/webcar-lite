"""Generacion del contrato de alquiler."""

import structlog
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError
from apps.reservations.invoiced import ensure_not_invoiced
from apps.settings_app.models import CompanySettings, TermsVersion

from .models import Contract, ContractStatus

logger = structlog.get_logger(__name__)


class ContractServiceError(ServiceError):
    """No se dan las condiciones para emitir el contrato."""


def contract_context(reservation, *, terms: TermsVersion) -> dict:
    """Todo lo que sale impreso, resuelto de una vez.

    Se arma aqui y no en la plantilla para que el PDF y la vista previa digan
    exactamente lo mismo, y para que se pueda probar sin renderizar nada.
    """
    entrega = getattr(reservation, "check_in", None)
    return {
        "empresa": CompanySettings.load(),
        "reservation": reservation,
        "cliente": reservation.customer,
        "vehiculo": reservation.vehicle,
        "entrega": entrega,
        "conductores": list(reservation.drivers.all()),
        "lineas": reservation.price_breakdown.get("lines", []),
        "desglose": reservation.price_breakdown,
        "cargos": list(reservation.charges.all()),
        "danos": list(reservation.damages.filter(is_preexisting=True)),
        "condiciones": terms,
        "emitido_el": timezone.now(),
    }


def render_contract_pdf(contract: Contract) -> bytes:
    """HTML del contrato pasado por WeasyPrint."""
    from django.utils import translation
    from weasyprint import HTML

    from apps.billing.pdf import document_language

    # El contrato sale en el idioma del cliente. Las condiciones generales son
    # texto de la empresa y van tal cual se publicaron.
    with translation.override(document_language(contract.reservation.customer)):
        contexto = contract_context(contract.reservation, terms=contract.terms_version)
        html = render_to_string("contracts/contract.html", contexto)
    return HTML(string=html, base_url=".").write_pdf()


@transaction.atomic
def request_contract(*, reservation, actor=None) -> Contract:
    """Encola la generacion del contrato y devuelve la fila enseguida.

    El mostrador no se queda esperando a WeasyPrint: se crea el contrato en
    estado "generandose", se manda la tarea a Celery y la pantalla avisa en
    cuanto esta listo.
    """
    ensure_not_invoiced(reservation)
    condiciones = TermsVersion.current()
    if condiciones is None:
        raise ContractServiceError(
            _(
                "No hay condiciones generales publicadas. Publica una version en "
                "Configuracion antes de emitir contratos."
            )
        )
    if reservation.customer_id is None:
        raise ContractServiceError(_("La reserva no tiene titular: no se puede emitir contrato."))

    contrato = Contract.objects.create(
        reservation=reservation,
        terms_version=condiciones,
        status=ContractStatus.PENDING,
        generated_by=actor if getattr(actor, "pk", None) else None,
    )

    # Se encola despues de confirmar la transaccion: si el alta se deshace, la
    # tarea no puede encontrarse una fila que no existe.
    transaction.on_commit(lambda: _encolar(contrato.pk))

    logger.info(
        "contrato_solicitado",
        contract_id=contrato.pk,
        reservation_number=reservation.number,
        terms_version=condiciones.version,
        actor_id=getattr(actor, "pk", None),
    )
    return contrato


def _encolar(contract_id: int) -> None:
    from .tasks import generate_contract

    generate_contract.delay(contract_id)


def build_contract(contract: Contract) -> Contract:
    """Genera el PDF y lo guarda. Lo llama la tarea de Celery."""
    try:
        pdf = render_contract_pdf(contract)
    except Exception as exc:  # el fallo se guarda y se ensena, no se traga
        contract.status = ContractStatus.FAILED
        contract.error = f"{type(exc).__name__}: {exc}"
        contract.save(update_fields=["status", "error", "updated_at"])
        logger.error("contrato_fallido", contract_id=contract.pk, error=contract.error)
        raise

    contract.file.save(contract.filename, ContentFile(pdf), save=False)
    contract.status = ContractStatus.READY
    contract.error = ""
    contract.generated_at = timezone.now()
    contract.save(update_fields=["file", "status", "error", "generated_at", "updated_at"])

    from apps.notifications.models import EmailKind
    from apps.notifications.services import queue_email

    queue_email(
        kind=EmailKind.CONTRACT,
        reservation=contract.reservation,
        context={"contract_id": contract.pk},
    )
    logger.info(
        "contrato_generado",
        contract_id=contract.pk,
        reservation_number=contract.reservation.number,
        bytes=len(pdf),
    )
    return contract
