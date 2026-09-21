"""Pagos online: del enlace al cobro apuntado.

Flujo:

1. En mostrador se crea el enlace (`create_link`) para una reserva: importe,
   concepto (anticipo, pago o fianza) y pasarela. Se envia por correo.
2. El cliente abre la pagina publica y va a la pasarela (`start_checkout`).
3. La pasarela avisa con una notificacion firmada. Solo entonces se apunta el
   cobro con `register_payment`, igual que uno de mostrador
   (`handle_stripe_event`, `handle_redsys_notification`).
4. La fianza queda retenida en la tarjeta: se libera o se cobra una parte
   (`release_deposit`, `capture_deposit`).

Todo lo que cambia el estado de un pago bloquea su fila: una notificacion
repetida (las pasarelas reintentan) no puede apuntar dos veces el mismo cobro.
"""

import secrets
from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.services import ServiceError

from .gateways import GatewayError, enabled_providers
from .gateways import redsys as pasarela_redsys
from .gateways import stripe as pasarela_stripe
from .models import (
    OnlinePayment,
    OnlinePurpose,
    OnlineStatus,
    PaymentMethod,
    PaymentType,
)
from .selectors import pending_amount

logger = structlog.get_logger(__name__)

PERMISO = "billing.add_onlinepayment"

TIPO_DE_COBRO = {
    OnlinePurpose.ADVANCE: PaymentType.ADVANCE,
    OnlinePurpose.PAYMENT: PaymentType.PAYMENT,
    OnlinePurpose.DEPOSIT: PaymentType.DEPOSIT,
}


class OnlinePaymentError(ServiceError):
    """No se puede crear o mover ese pago online."""


def _url(nombre: str, token: str) -> str:
    return settings.PUBLIC_BASE_URL + reverse(nombre, args=[token])


def _auditar(pago, mensaje, actor=None, **cambios):
    audit.record(
        AuditAction.ONLINE_PAYMENT,
        mensaje,
        obj=pago,
        actor=actor,
        reservation=pago.reservation,
        changes=cambios,
    )


@transaction.atomic
def create_link(*, reservation, provider: str, purpose: str, amount: Decimal, actor=None):
    """Crea el enlace de pago. El importe de un cobro no puede pasar del pendiente."""
    if actor is None or not actor.has_perm(PERMISO):
        raise PermissionDenied(
            _("Tu usuario no puede pedir pagos online (%(p)s).") % {"p": PERMISO}
        )
    if provider not in dict(enabled_providers()):
        raise OnlinePaymentError(_("Esa pasarela no está configurada."))
    importe = Decimal(amount).quantize(Decimal("0.01"))
    if importe <= 0:
        raise OnlinePaymentError(_("El importe tiene que ser mayor que cero."))
    if purpose != OnlinePurpose.DEPOSIT and importe > pending_amount(reservation):
        raise OnlinePaymentError(
            _("%(importe)s € pasa del pendiente de la reserva (%(pendiente)s €).")
            % {"importe": importe, "pendiente": pending_amount(reservation)}
        )
    pago = OnlinePayment.objects.create(
        token=secrets.token_urlsafe(24),
        reservation=reservation,
        provider=provider,
        purpose=purpose,
        amount=importe,
        expires_at=timezone.now() + timedelta(hours=settings.ONLINE_PAYMENT_LINK_HOURS),
        created_by=actor,
    )
    _auditar(
        pago,
        _("Enlace de pago de %(importe)s € (%(concepto)s) para %(numero)s")
        % {
            "importe": importe,
            "concepto": pago.get_purpose_display(),
            "numero": reservation.number,
        },
        actor,
        provider=provider,
    )
    return pago


def _descripcion(pago) -> str:
    return f"{pago.get_purpose_display()} · {pago.reservation.number}"


@transaction.atomic
def start_checkout(*, token: str) -> dict:
    """Lo que necesita la pagina publica para mandar al cliente a pagar.

    Devuelve `{"redirect": url}` (Stripe) o `{"form": {...}}` (Redsys).
    """
    pago = (
        OnlinePayment.objects.select_for_update(of=("self",))
        .select_related("reservation__customer")
        .get(token=token)
    )
    if not pago.is_open:
        raise OnlinePaymentError(_("Este enlace de pago ya no está disponible."))
    cliente = pago.reservation.customer
    idioma = getattr(cliente, "language", "es") or "es"
    ok = _url("billing:pay_result", pago.token) + "?resultado=ok"
    ko = _url("billing:pay_result", pago.token) + "?resultado=ko"

    try:
        if pago.provider == "stripe":
            sesion = pasarela_stripe.create_checkout(
                token=pago.token,
                amount=pago.amount,
                description=_descripcion(pago),
                success_url=ok,
                cancel_url=ko,
                email=getattr(cliente, "email", "") or "",
                locale=idioma,
                hold=pago.is_hold,
            )
            pago.provider_ref = sesion["id"]
            pago.status = OnlineStatus.PENDING
            pago.save(update_fields=["provider_ref", "status", "updated_at"])
            return {"redirect": sesion["url"]}

        if pago.provider == "redsys":
            # Un pedido nuevo por intento: Redsys no acepta repetir numero.
            pago.provider_ref = pasarela_redsys.order_number(
                pago.pk, int(timezone.now().timestamp())
            )
            pago.status = OnlineStatus.PENDING
            pago.save(update_fields=["provider_ref", "status", "updated_at"])
            return {
                "form": pasarela_redsys.payment_form(
                    pedido=pago.provider_ref,
                    amount=pago.amount,
                    description=_descripcion(pago),
                    notify_url=settings.PUBLIC_BASE_URL + reverse("billing:redsys_notify"),
                    ok_url=ok,
                    ko_url=ko,
                    locale=idioma,
                    hold=pago.is_hold,
                )
            }
    except GatewayError as exc:
        # El detalle es tecnico y en espanol (claves, codigos del banco): va al
        # log. El cliente lee un aviso en su idioma.
        logger.error("pago_online_pasarela_fallida", pago_id=pago.pk, error=str(exc))
        raise OnlinePaymentError(
            _("No hemos podido conectar con el banco. Inténtalo de nuevo en unos minutos.")
        ) from exc
    raise OnlinePaymentError(_("Pasarela desconocida."))


def _apuntar_cobro(pago, referencia: str):
    """El cobro, igual que uno de mostrador. Una sola vez por pago."""
    from .services import register_payment

    if pago.payment_id:
        return pago.payment
    cobro = register_payment(
        reservation=pago.reservation,
        amount=pago.amount,
        method=PaymentMethod.CARD,
        payment_type=TIPO_DE_COBRO[pago.purpose],
        office=pago.reservation.pickup_office,
        reference=referencia[:60],
        notes=str(_("Pago online (%(pasarela)s)") % {"pasarela": pago.provider}),
        from_gateway=True,
    )
    pago.payment = cobro
    return cobro


@transaction.atomic
def _confirmar(pago_id: int, *, autorizado: bool, referencia: str, intent: str = "", evento=None):
    pago = (
        OnlinePayment.objects.select_for_update(of=("self",))
        .select_related("reservation")
        .get(pk=pago_id)
    )
    if pago.status in (
        OnlineStatus.PAID,
        OnlineStatus.AUTHORIZED,
        OnlineStatus.CAPTURED,
        OnlineStatus.RELEASED,
    ):
        return pago  # notificacion repetida: ya esta apuntado
    pago.last_event = evento or {}
    if intent:
        pago.provider_intent = intent
    if not autorizado:
        pago.status = OnlineStatus.FAILED
        pago.save()
        _auditar(
            pago, _("Pago online rechazado en %(numero)s") % {"numero": pago.reservation.number}
        )
        return pago
    _apuntar_cobro(pago, referencia)
    pago.status = OnlineStatus.AUTHORIZED if pago.is_hold else OnlineStatus.PAID
    pago.save()
    _auditar(
        pago,
        (
            _("Fianza de %(importe)s € retenida online en %(numero)s")
            if pago.is_hold
            else _("Pago online de %(importe)s € recibido en %(numero)s")
        )
        % {"importe": pago.amount, "numero": pago.reservation.number},
    )
    _avisar_al_cliente(pago)
    return pago


def _avisar_al_cliente(pago):
    """Correo de "hemos recibido tu pago", si los correos estan activos."""
    try:
        from apps.notifications.services import queue_email
    except ImportError:  # pragma: no cover - notificaciones es opcional
        return
    queue_email(kind="payment_received", reservation=pago.reservation, context={"pago_id": pago.pk})


def handle_stripe_event(evento: dict):
    """Aplica un evento de Stripe ya verificado. Devuelve el pago afectado o None."""
    tipo = evento.get("type", "")
    objeto = (evento.get("data") or {}).get("object") or {}
    token = (objeto.get("metadata") or {}).get("token") or objeto.get("client_reference_id")
    pago = OnlinePayment.objects.filter(token=token).first() if token else None
    if pago is None:
        return None
    if tipo in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        estado_del_pago = objeto.get("payment_status")
        completa = objeto.get("status") == "complete"
        # Con captura manual (la fianza) Stripe dice "unpaid" aunque el importe
        # este retenido. En un pago normal, "unpaid" es un pago asincrono que
        # aun no ha llegado: se espera a async_payment_succeeded.
        pagado = completa and (
            estado_del_pago == "paid" or (pago.is_hold and estado_del_pago == "unpaid")
        )
        if not pagado:
            return pago
        return _confirmar(
            pago.pk,
            autorizado=True,
            referencia=objeto.get("payment_intent") or objeto.get("id", ""),
            intent=objeto.get("payment_intent") or "",
            evento={"type": tipo, "id": evento.get("id")},
        )
    if tipo in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        with transaction.atomic():
            pago = OnlinePayment.objects.select_for_update(of=("self",)).get(pk=pago.pk)
            if pago.status in (OnlineStatus.CREATED, OnlineStatus.PENDING):
                pago.status = (
                    OnlineStatus.EXPIRED if tipo.endswith("expired") else OnlineStatus.FAILED
                )
                pago.last_event = {"type": tipo, "id": evento.get("id")}
                pago.save()
        return pago
    return pago


def handle_redsys_notification(parametros: str, firma: str):
    """Aplica una notificacion de Redsys. Lanza GatewayError si la firma no vale."""
    datos = pasarela_redsys.parse_notification(parametros, firma)
    pedido = datos.get("Ds_Order", "")
    pago = OnlinePayment.objects.filter(provider="redsys", provider_ref=pedido).first()
    if pago is None:
        return None
    return _confirmar(
        pago.pk,
        autorizado=pasarela_redsys.is_authorized(datos),
        referencia=f"{pedido}/{datos.get('Ds_AuthorisationCode', '')}",
        evento={k: datos.get(k) for k in ("Ds_Response", "Ds_Order", "Ds_AuthorisationCode")},
    )


@transaction.atomic
def capture_deposit(*, online_payment, amount: Decimal, actor=None):
    """Cobra parte de la fianza retenida; el resto vuelve a la tarjeta.

    En caja: la fianza apuntada se devuelve por lo que no se cobra, y lo
    cobrado sigue como fianza retenida hasta aplicarlo a los cargos.
    """
    from .services import register_payment

    if actor is None or not actor.has_perm("billing.change_onlinepayment"):
        raise PermissionDenied(_("Tu usuario no puede mover fianzas online."))
    pago = (
        OnlinePayment.objects.select_for_update(of=("self",))
        .select_related("reservation")
        .get(pk=online_payment.pk)
    )
    if pago.status != OnlineStatus.AUTHORIZED:
        raise OnlinePaymentError(_("No hay fianza retenida en ese pago."))
    importe = Decimal(amount).quantize(Decimal("0.01"))
    if importe <= 0 or importe > pago.amount:
        raise OnlinePaymentError(
            _("Se puede cobrar entre 0,01 € y %(maximo)s €.") % {"maximo": pago.amount}
        )
    try:
        if pago.provider == "stripe":
            pasarela_stripe.capture(pago.provider_intent, importe)
        else:
            pasarela_redsys.capture(pago.provider_ref, importe)
    except GatewayError as exc:
        raise OnlinePaymentError(str(exc)) from exc
    sobrante = pago.amount - importe
    if sobrante > 0:
        register_payment(
            reservation=pago.reservation,
            amount=sobrante,
            method=PaymentMethod.CARD,
            payment_type=PaymentType.DEPOSIT_RETURN,
            office=pago.reservation.pickup_office,
            reference=pago.provider_ref[:60],
            notes=str(_("Parte de la fianza online no cobrada")),
            actor=actor,
        )
    pago.status = OnlineStatus.CAPTURED
    pago.captured_amount = importe
    pago.save()
    _auditar(
        pago,
        _("Fianza online de %(numero)s: cobrados %(importe)s €")
        % {"numero": pago.reservation.number, "importe": importe},
        actor,
    )
    return pago


@transaction.atomic
def release_deposit(*, online_payment, actor=None):
    """Libera la fianza retenida: no se cobra nada."""
    from .services import register_payment

    if actor is None or not actor.has_perm("billing.change_onlinepayment"):
        raise PermissionDenied(_("Tu usuario no puede mover fianzas online."))
    pago = (
        OnlinePayment.objects.select_for_update(of=("self",))
        .select_related("reservation")
        .get(pk=online_payment.pk)
    )
    if pago.status != OnlineStatus.AUTHORIZED:
        raise OnlinePaymentError(_("No hay fianza retenida en ese pago."))
    try:
        if pago.provider == "stripe":
            pasarela_stripe.release(pago.provider_intent)
        else:
            pasarela_redsys.release(pago.provider_ref, pago.amount)
    except GatewayError as exc:
        raise OnlinePaymentError(str(exc)) from exc
    register_payment(
        reservation=pago.reservation,
        amount=pago.amount,
        method=PaymentMethod.CARD,
        payment_type=PaymentType.DEPOSIT_RETURN,
        office=pago.reservation.pickup_office,
        reference=pago.provider_ref[:60],
        notes=str(_("Fianza online liberada")),
        actor=actor,
    )
    pago.status = OnlineStatus.RELEASED
    pago.save()
    _auditar(
        pago, _("Fianza online de %(numero)s liberada") % {"numero": pago.reservation.number}, actor
    )
    return pago


@transaction.atomic
def cancel_link(*, online_payment, actor=None):
    pago = OnlinePayment.objects.select_for_update(of=("self",)).get(pk=online_payment.pk)
    if pago.status not in (OnlineStatus.CREATED, OnlineStatus.PENDING):
        raise OnlinePaymentError(_("Ese pago ya no se puede anular desde aquí."))
    pago.status = OnlineStatus.CANCELLED
    pago.save(update_fields=["status", "updated_at"])
    _auditar(pago, _("Enlace de pago anulado"), actor)
    return pago


def expire_old_links() -> int:
    """Periodica: los enlaces que nadie pago dejan de valer."""
    return OnlinePayment.objects.filter(
        status__in=(OnlineStatus.CREATED, OnlineStatus.PENDING), expires_at__lt=timezone.now()
    ).update(status=OnlineStatus.EXPIRED, updated_at=timezone.now())
