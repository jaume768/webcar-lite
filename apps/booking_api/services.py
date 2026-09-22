"""Reglas de la API de reservas. Las vistas traducen JSON; aqui se decide.

Nada de esto tiene un camino propio hacia la base de datos de reservas: el
alta va por `create_quick_reservation` (que recuenta la disponibilidad con el
grupo bloqueado), la cancelacion por la maquina de estados y el enlace de pago
por `billing.online`. Lo unico que anade la API es quien es el cliente, que
oficinas puede vender y la idempotencia de las peticiones.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.availability.services import AvailabilityError, get_available_categories
from apps.billing.gateways import enabled_providers
from apps.billing.models import OnlinePurpose
from apps.core.services import ServiceError
from apps.customers.models import Customer
from apps.customers.services import save_customer
from apps.fleet.models import VehicleCategory
from apps.offices.models import Office
from apps.pricing.dto import ExtraRequest
from apps.pricing.models import Channel, Extra
from apps.pricing.services import InvalidRentalPeriod, PricingError
from apps.reservations.models import Reservation, ReservationStatus
from apps.reservations.services import create_quick_reservation, quote

from . import keys
from .models import ApiClient, ApiReservation

logger = structlog.get_logger(__name__)

#: Rol del usuario tecnico que hay detras de cada cliente de la API.
ROL_API = "api_web"


class BookingApiError(ServiceError):
    """La peticion es correcta, pero no se puede atender. Lleva un codigo estable."""

    def __init__(self, message, *, code: str, status: int = 422, fields: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.fields = fields or {}


# ---------------------------------------------------------------------------
# Clientes de la API
# ---------------------------------------------------------------------------


@transaction.atomic
def create_client(*, name: str, offices, actor=None) -> tuple[ApiClient, str]:
    """Da de alta una web con su usuario tecnico. Devuelve la clave en claro, una vez."""
    from apps.accounts.models import Role

    rol = Role.objects.filter(code=ROL_API).first()
    if rol is None:
        raise BookingApiError(
            _("Falta el rol %(rol)s: ejecuta sync_roles.") % {"rol": ROL_API}, code="setup"
        )
    oficinas = list(offices)
    if not oficinas:
        raise BookingApiError(_("Una web tiene que vender al menos una oficina."), code="setup")

    base = slugify(name) or "web"
    correo = f"api-{base}@api.invalid"
    numero = 1
    while get_user_model().objects.filter(email=correo).exists():
        numero += 1
        correo = f"api-{base}-{numero}@api.invalid"
    usuario = get_user_model().objects.create_user(
        email=correo, password=None, first_name="API", last_name=name[:120], role=rol
    )
    usuario.offices.set(oficinas)

    clave, prefijo, huella = keys.generate()
    cliente = ApiClient.objects.create(name=name, user=usuario, key_prefix=prefijo, key_hash=huella)
    audit.record(
        AuditAction.CREATE,
        _("Cliente de la API %(nombre)s creado") % {"nombre": name},
        obj=cliente,
        actor=actor,
        changes={"offices": [o.code for o in oficinas]},
    )
    return cliente, clave


@transaction.atomic
def rotate_key(*, client: ApiClient, actor=None) -> str:
    """Nueva clave; la anterior deja de valer en el acto."""
    clave, prefijo, huella = keys.generate()
    client.key_prefix, client.key_hash = prefijo, huella
    client.save(update_fields=["key_prefix", "key_hash", "updated_at"])
    audit.record(
        AuditAction.UPDATE,
        _("Clave de la API de %(nombre)s renovada") % {"nombre": client.name},
        obj=client,
        actor=actor,
    )
    return clave


def authenticate(clave: str) -> ApiClient | None:
    """El cliente activo de esa clave, o None. No dice por que no vale."""
    prefijo = keys.prefix_of(clave)
    if not prefijo:
        return None
    cliente = (
        ApiClient.objects.select_related("user")
        .filter(key_prefix=prefijo, is_active=True, user__is_active=True)
        .first()
    )
    if cliente is None or not keys.matches(clave, cliente.key_hash):
        return None
    ahora = timezone.now()
    if cliente.last_used_at is None or ahora - cliente.last_used_at > timedelta(minutes=5):
        ApiClient.objects.filter(pk=cliente.pk).update(last_used_at=ahora)
    return cliente


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------


def offices_for(client: ApiClient):
    return Office.objects.filter(is_active=True, users=client.user).order_by("name")


def office_for(client: ApiClient, code: str, campo: str) -> Office:
    """La oficina, si esta web puede venderla. Si no, como si no existiera."""
    oficina = offices_for(client).filter(code=code).first()
    if oficina is None:
        raise BookingApiError(
            _("Oficina desconocida."), code="unknown_office", fields={campo: [code]}
        )
    return oficina


def extras_catalog():
    return Extra.objects.filter(is_active=True).order_by("name")


def _comprobar_antelacion(pickup_at) -> None:
    minimo = timezone.now() + timedelta(minutes=settings.BOOKING_API_MIN_LEAD_MINUTES)
    if pickup_at < minimo:
        raise BookingApiError(
            _("La recogida tiene que ser al menos %(n)s minutos a partir de ahora.")
            % {"n": settings.BOOKING_API_MIN_LEAD_MINUTES},
            code="too_soon",
            fields={"pickup_at": ["too_soon"]},
        )


@dataclass(frozen=True)
class Offer:
    category: VehicleCategory
    free: int
    breakdown: object  # pricing.dto.PriceBreakdown


def search(*, client, pickup_office, return_office, pickup_at, return_at) -> list[Offer]:
    """Categorias con hueco y precio web para ese periodo.

    Una categoria sin tarifa web para esas fechas no se ofrece: la web no puede
    vender lo que el mostrador no sabria cobrar.
    """
    _comprobar_antelacion(pickup_at)
    try:
        disponibles = get_available_categories(pickup_office, pickup_at, return_at)
    except AvailabilityError as exc:
        raise BookingApiError(str(exc), code="invalid_period") from exc

    ofertas = []
    for fila in disponibles:
        try:
            desglose = quote(
                category=fila.category,
                pickup_office=pickup_office,
                return_office=return_office,
                pickup_at=pickup_at,
                return_at=return_at,
                channel=Channel.WEB,
            )
        except (PricingError, InvalidRentalPeriod):
            continue
        ofertas.append(
            Offer(category=fila.category, free=fila.availability.free, breakdown=desglose)
        )
    return ofertas


def extra_requests(lineas: list[dict]) -> tuple[ExtraRequest, ...]:
    codigos = [linea["code"] for linea in lineas]
    catalogo = {extra.code: extra for extra in extras_catalog().filter(code__in=codigos)}
    desconocidos = [codigo for codigo in codigos if codigo not in catalogo]
    if desconocidos:
        raise BookingApiError(
            _("Extras desconocidos: %(codigos)s.") % {"codigos": ", ".join(desconocidos)},
            code="unknown_extra",
            fields={"extras": desconocidos},
        )
    peticiones = []
    for linea in lineas:
        extra = catalogo[linea["code"]]
        cantidad = linea.get("quantity") or 1
        if extra.max_quantity and cantidad > extra.max_quantity:
            raise BookingApiError(
                _("%(extra)s: como mucho %(n)s.") % {"extra": extra.name, "n": extra.max_quantity},
                code="extra_quantity",
                fields={"extras": [extra.code]},
            )
        peticiones.append(ExtraRequest(extra=extra, quantity=cantidad))
    return tuple(peticiones)


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


def request_fingerprint(cuerpo: dict) -> str:
    return hashlib.sha256(
        json.dumps(cuerpo, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


#: Datos de contacto que la web puede completar en un cliente que ya existe.
#: Nunca se pisa lo que ya hay: el cliente puede tener datos revisados en
#: mostrador (y en facturas emitidas) que una web no debe cambiar.
RELLENABLES = ("email", "phone", "birth_date", "licence_number", "address", "city", "postal_code")


def _cliente(datos: dict, oficina, actor) -> Customer:
    existente = (
        Customer.objects.select_for_update()
        .filter(document_type=datos["document_type"], document_number=datos["document_number"])
        .first()
    )
    if existente is None:
        return save_customer(customer=Customer(**datos), actor=actor, office=oficina)

    if existente.is_blacklisted or not existente.is_active:
        # A la web no se le dice por que: el motivo es interno.
        raise BookingApiError(
            _("No podemos completar esta reserva online. Contacta con la oficina."),
            code="not_bookable",
        )
    cambios = [c for c in RELLENABLES if not getattr(existente, c) and datos.get(c)]
    for campo in cambios:
        setattr(existente, campo, datos[campo])
    if cambios:
        existente.save(update_fields=cambios)
    return existente


def _reserva_existente(client, idempotency_key: str, huella: str) -> Reservation | None:
    previa = (
        ApiReservation.objects.select_related("reservation")
        .filter(api_client=client, idempotency_key=idempotency_key)
        .first()
    )
    if previa is None:
        return None
    if previa.request_hash != huella:
        raise BookingApiError(
            _("Esa clave de idempotencia ya se uso con otra peticion."),
            code="idempotency_conflict",
            status=409,
        )
    return previa.reservation


@dataclass(frozen=True)
class BookingResult:
    reservation: Reservation
    created: bool
    payment: object | None = None  # billing.OnlinePayment


def create_booking(
    *,
    client: ApiClient,
    idempotency_key: str,
    fingerprint: str,
    category_code: str,
    pickup_office,
    return_office,
    pickup_at,
    return_at,
    customer_data: dict,
    extras=(),
    notes: str = "",
    external_ref: str = "",
    payment: dict | None = None,
) -> BookingResult:
    """Crea la reserva web. Repetir la peticion con la misma clave no duplica nada."""
    previa = _reserva_existente(client, idempotency_key, fingerprint)
    if previa is not None:
        return BookingResult(reservation=previa, created=False)

    try:
        with transaction.atomic():
            reserva, pago = _crear(
                client=client,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                category_code=category_code,
                pickup_office=pickup_office,
                return_office=return_office,
                pickup_at=pickup_at,
                return_at=return_at,
                customer_data=customer_data,
                extras=extras,
                notes=notes,
                external_ref=external_ref,
                payment=payment,
            )
    except IntegrityError:
        # Dos peticiones con la misma clave a la vez: una gana, la otra se
        # deshace entera (reserva incluida) y devuelve la que gano.
        previa = _reserva_existente(client, idempotency_key, fingerprint)
        if previa is None:
            raise
        return BookingResult(reservation=previa, created=False)
    return BookingResult(reservation=reserva, created=True, payment=pago)


def _crear(
    *,
    client,
    idempotency_key,
    fingerprint,
    category_code,
    pickup_office,
    return_office,
    pickup_at,
    return_at,
    customer_data,
    extras,
    notes,
    external_ref,
    payment,
):
    actor = client.user
    if not actor.has_perm("reservations.add_reservation"):
        raise PermissionDenied(_("Este cliente de la API no puede crear reservas."))
    _comprobar_antelacion(pickup_at)
    categoria = VehicleCategory.objects.filter(code=category_code, is_active=True).first()
    if categoria is None:
        raise BookingApiError(
            _("Categoría desconocida."),
            code="unknown_category",
            fields={"category": [category_code]},
        )
    if payment and payment["provider"] not in dict(enabled_providers()):
        raise BookingApiError(
            _("Esa pasarela de pago no está configurada."),
            code="payment_provider_disabled",
            fields={"payment": [payment["provider"]]},
        )

    cliente = _cliente(customer_data, pickup_office, actor)
    try:
        reserva = create_quick_reservation(
            category=categoria,
            pickup_office=pickup_office,
            return_office=return_office,
            pickup_at=pickup_at,
            return_at=return_at,
            customer=cliente,
            extras=extras,
            channel=Channel.WEB,
            notes=notes,
            internal_notes=str(_("Entrada por la API: %(web)s") % {"web": client.name})
            + (f" · {external_ref}" if external_ref else ""),
            actor=actor,
            origin=str(_("Reserva web (API: %(web)s)") % {"web": client.name}),
        )
    except AvailabilityError as exc:
        raise BookingApiError(
            _("Ya no queda disponibilidad para esa categoría en esas fechas."),
            code="not_available",
            status=409,
        ) from exc
    except (PricingError, InvalidRentalPeriod) as exc:
        raise BookingApiError(
            _("Esa categoría no tiene precio web para esas fechas."), code="no_rate"
        ) from exc

    ApiReservation.objects.create(
        api_client=client,
        reservation=reserva,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
        external_ref=external_ref,
    )
    pago = None
    if payment:
        pago = payment_link(
            client=client,
            reservation=reserva,
            provider=payment["provider"],
            purpose=payment["purpose"],
            amount=payment.get("amount"),
        )
    logger.info(
        "reserva_por_api",
        api_client_id=client.pk,
        reservation_number=reserva.number,
        total=str(reserva.total),
    )
    return reserva, pago


# ---------------------------------------------------------------------------
# Despues del alta
# ---------------------------------------------------------------------------


def reservation_for(client: ApiClient, number: str) -> Reservation:
    """Solo las reservas que creo esta misma web."""
    reserva = (
        Reservation.objects.select_related("category", "pickup_office", "return_office", "customer")
        .filter(number=number, api_origin__api_client=client)
        .first()
    )
    if reserva is None:
        raise BookingApiError(_("Reserva no encontrada."), code="not_found", status=404)
    return reserva


def cancel(*, client: ApiClient, reservation: Reservation) -> Reservation:
    """Cancela por la maquina de estados, con su politica de cancelacion."""
    from apps.reservations.state_machine import (
        InvalidTransition,
        TransitionRefused,
        transition,
    )

    if reservation.status not in (ReservationStatus.PENDING, ReservationStatus.CONFIRMED):
        raise BookingApiError(
            _("Esta reserva ya no se puede cancelar online."), code="not_cancellable", status=409
        )
    try:
        return transition(
            reservation,
            ReservationStatus.CANCELLED,
            client.user,
            reason=str(_("Cancelada por el cliente desde la web (%(web)s)") % {"web": client.name}),
        )
    except (InvalidTransition, TransitionRefused) as exc:
        raise BookingApiError(str(exc), code="not_cancellable", status=409) from exc
    except ServiceError as exc:  # reserva facturada
        raise BookingApiError(
            _("Esta reserva ya no se puede cancelar online."), code="not_cancellable", status=409
        ) from exc


def payment_link(*, client, reservation, provider: str, purpose: str, amount=None):
    """Enlace de pago Stripe o Redsys para la reserva, a eleccion de la web."""
    from apps.billing.online import OnlinePaymentError, create_link
    from apps.billing.selectors import pending_amount

    if reservation.status not in (ReservationStatus.PENDING, ReservationStatus.CONFIRMED):
        raise BookingApiError(
            _("Esta reserva no admite pagos online."), code="not_payable", status=409
        )
    if provider not in dict(enabled_providers()):
        raise BookingApiError(
            _("Esa pasarela de pago no está configurada."),
            code="payment_provider_disabled",
            fields={"provider": [provider]},
        )
    importe = Decimal(amount) if amount else pending_amount(reservation)
    if purpose == OnlinePurpose.DEPOSIT:
        raise BookingApiError(_("La fianza se gestiona en la oficina."), code="not_payable")
    try:
        return create_link(
            reservation=reservation,
            provider=provider,
            purpose=purpose,
            amount=importe,
            actor=client.user,
        )
    except OnlinePaymentError as exc:
        raise BookingApiError(str(exc), code="payment_rejected") from exc
