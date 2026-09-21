"""Paginas publicas del pago online y notificaciones de las pasarelas.

Son las unicas vistas de facturacion sin sesion. Lo que ven no dice nada que
el cliente no sepa ya (importe, concepto, numero de reserva) y solo se llega
con el token del enlace. Las notificaciones no confian en nada que no venga
firmado por la pasarela.
"""

import json

import structlog
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.utils import translation
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.services import ServiceError

from .gateways import GatewayError
from .gateways import stripe as pasarela_stripe
from .models import OnlinePayment

logger = structlog.get_logger(__name__)


def _pago(token: str) -> OnlinePayment:
    pago = (
        OnlinePayment.objects.select_related("reservation__customer", "reservation__pickup_office")
        .filter(token=token)
        .first()
    )
    if pago is None:
        raise Http404
    return pago


def _idioma(pago) -> str:
    cliente = pago.reservation.customer
    return getattr(cliente, "language", "") or "es"


def _contexto(pago) -> dict:
    from apps.settings_app.models import CompanySettings

    empresa = CompanySettings.load()
    return {
        "pago": pago,
        "reserva": pago.reservation,
        "empresa": empresa,
        "logo_url": empresa.logo.url if empresa.logo else "",
    }


@method_decorator(login_not_required, name="dispatch")
class PayView(View):
    """El enlace que recibe el cliente: resumen y boton de pagar."""

    def get(self, request, token):
        pago = _pago(token)
        with translation.override(_idioma(pago)):
            return render(request, "billing/public/pay.html", _contexto(pago))

    def post(self, request, token):
        from .online import start_checkout

        pago = _pago(token)
        with translation.override(_idioma(pago)):
            try:
                destino = start_checkout(token=token)
            except ServiceError as exc:
                contexto = _contexto(pago)
                contexto["error"] = str(exc)
                return render(request, "billing/public/pay.html", contexto, status=409)
            if "redirect" in destino:
                return HttpResponseRedirect(destino["redirect"])
            return render(
                request, "billing/public/redirect.html", {**_contexto(pago), **destino["form"]}
            )


@method_decorator(login_not_required, name="dispatch")
class PayResultView(View):
    """Vuelta desde la pasarela. Informa; el cobro lo apunta la notificacion."""

    def get(self, request, token):
        pago = _pago(token)
        with translation.override(_idioma(pago)):
            contexto = _contexto(pago)
            contexto["resultado"] = request.GET.get("resultado", "")
            return render(request, "billing/public/result.html", contexto)


@method_decorator([login_not_required, csrf_exempt], name="dispatch")
class StripeWebhookView(View):
    def post(self, request):
        from .online import handle_stripe_event

        firma = request.headers.get("Stripe-Signature", "")
        if not pasarela_stripe.verify_webhook(request.body, firma):
            logger.warning("stripe_firma_no_valida")
            return HttpResponseBadRequest("firma no valida")
        try:
            evento = json.loads(request.body)
        except ValueError:
            return HttpResponseBadRequest("json no valido")
        handle_stripe_event(evento)
        return HttpResponse("ok")


@method_decorator([login_not_required, csrf_exempt], name="dispatch")
class RedsysNotifyView(View):
    def post(self, request):
        from .online import handle_redsys_notification

        try:
            handle_redsys_notification(
                request.POST.get("Ds_MerchantParameters", ""), request.POST.get("Ds_Signature", "")
            )
        except GatewayError as exc:
            logger.warning("redsys_notificacion_rechazada", error=str(exc))
            return HttpResponseBadRequest("firma no valida")
        return HttpResponse("ok")
