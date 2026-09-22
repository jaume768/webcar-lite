"""Endpoints JSON de la API de reservas (v1).

Se llama de servidor a servidor: la web del cliente guarda la clave en su
backend y nunca la manda al navegador. Por eso no hay CORS ni sesion: cada
peticion se autentica con `Authorization: Bearer <clave>`.

Errores, siempre con la misma forma:

    {"error": {"code": "not_available", "message": "...", "fields": {...}}}
"""

import json

import structlog
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils import timezone, translation
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.billing.gateways import enabled_providers

from . import serializers, services
from .forms import (
    ApiCustomerForm,
    BookingForm,
    ExtraLineForm,
    PaymentRequestForm,
    PeriodForm,
)
from .services import BookingApiError

logger = structlog.get_logger(__name__)


def _json(datos, status=200, **kwargs):
    return JsonResponse(datos, status=status, encoder=DjangoJSONEncoder, **kwargs)


def error(code: str, message, status: int, fields: dict | None = None):
    return _json(
        {"error": {"code": code, "message": str(message), "fields": fields or {}}}, status=status
    )


def _errores_de(form, prefijo: str = "") -> dict:
    return {
        f"{prefijo}{campo}": [str(m) for m in mensajes] for campo, mensajes in form.errors.items()
    }


class Invalid(Exception):
    def __init__(self, fields: dict):
        self.fields = fields


@method_decorator([csrf_exempt, login_not_required], name="dispatch")
class ApiView(View):
    """Autenticacion por clave, limite de peticiones y errores en JSON."""

    http_method_names = ["get", "post"]

    def dispatch(self, request, *args, **kwargs):
        cabecera = request.headers.get("Authorization", "")
        clave = cabecera[7:].strip() if cabecera.startswith("Bearer ") else ""
        cliente = services.authenticate(clave)
        if cliente is None:
            return error("unauthorized", _("Clave de API ausente o no válida."), 401)
        if self._limite_superado(cliente):
            return error("rate_limited", _("Demasiadas peticiones. Espera un minuto."), 429)
        self.api_client = cliente
        with translation.override(settings.LANGUAGE_CODE):
            try:
                return super().dispatch(request, *args, **kwargs)
            except Invalid as exc:
                return error("invalid", _("Hay datos que no son válidos."), 400, exc.fields)
            except BookingApiError as exc:
                return error(exc.code, exc, exc.status, exc.fields)
            except PermissionDenied as exc:
                return error("forbidden", exc, 403)

    def _limite_superado(self, cliente) -> bool:
        limite = settings.BOOKING_API_RATE_LIMIT_PER_MINUTE
        if not limite:
            return False
        clave = f"booking_api:{cliente.pk}:{timezone.now():%Y%m%d%H%M}"
        cache.add(clave, 0, timeout=90)
        try:
            return cache.incr(clave) > limite
        except ValueError:
            return False

    def body(self) -> dict:
        try:
            datos = json.loads(self.request.body or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise Invalid({"body": [_("El cuerpo tiene que ser JSON válido.")]}) from exc
        if not isinstance(datos, dict):
            raise Invalid({"body": [_("El cuerpo tiene que ser un objeto JSON.")]})
        return datos

    def valid(self, form, prefijo: str = ""):
        if not form.is_valid():
            raise Invalid(_errores_de(form, prefijo))
        return form.cleaned_data

    def period(self, datos) -> dict:
        periodo = self.valid(PeriodForm(datos))
        recogida = services.office_for(self.api_client, periodo["pickup_office"], "pickup_office")
        devolucion = (
            services.office_for(self.api_client, periodo["return_office"], "return_office")
            if periodo.get("return_office")
            else recogida
        )
        return {
            "pickup_office": recogida,
            "return_office": devolucion,
            "pickup_at": periodo["pickup_at"],
            "return_at": periodo["return_at"],
        }


class OfficesView(ApiView):
    def get(self, request):
        oficinas = services.offices_for(self.api_client)
        return _json({"results": [serializers.office(o) for o in oficinas]})


class ExtrasView(ApiView):
    def get(self, request):
        return _json({"results": [serializers.extra(e) for e in services.extras_catalog()]})


class PaymentProvidersView(ApiView):
    def get(self, request):
        return _json(
            {
                "results": [
                    {"code": code, "name": str(nombre)} for code, nombre in enabled_providers()
                ]
            }
        )


class AvailabilityView(ApiView):
    def get(self, request):
        periodo = self.period(request.GET)
        ofertas = services.search(client=self.api_client, **periodo)
        return _json({"results": [serializers.offer(o) for o in ofertas]})


class ReservationCreateView(ApiView):
    def post(self, request):
        clave = request.headers.get("Idempotency-Key", "").strip()
        if not clave or len(clave) > 80:
            raise Invalid({"Idempotency-Key": [_("Cabecera obligatoria, hasta 80 caracteres.")]})
        datos = self.body()
        reserva = self.valid(BookingForm(datos))
        periodo = self.period(datos)

        cliente_form = ApiCustomerForm(datos.get("customer") or {})
        cliente = self.valid(cliente_form, "customer.")

        lineas = datos.get("extras") or []
        if not isinstance(lineas, list):
            raise Invalid({"extras": [_("Tiene que ser una lista.")]})
        extras = [
            self.valid(ExtraLineForm(linea), f"extras.{i}.") for i, linea in enumerate(lineas)
        ]

        pago = None
        if datos.get("payment"):
            pago = self.valid(PaymentRequestForm(datos["payment"]), "payment.")

        resultado = services.create_booking(
            client=self.api_client,
            idempotency_key=clave,
            fingerprint=services.request_fingerprint(datos),
            category_code=reserva["category"],
            customer_data={
                campo: valor for campo, valor in cliente.items() if campo in cliente_form.data
            },
            extras=services.extra_requests(extras),
            notes=reserva.get("notes", ""),
            external_ref=reserva.get("external_ref", ""),
            payment=pago,
            **periodo,
        )
        pagos = [resultado.payment] if resultado.payment else []
        if not resultado.created:
            pagos = list(resultado.reservation.online_payments.order_by("created_at"))
        return _json(
            serializers.reservation(resultado.reservation, pagos=pagos),
            status=201 if resultado.created else 200,
        )


class ReservationDetailView(ApiView):
    def get(self, request, number):
        reserva = services.reservation_for(self.api_client, number)
        pagos = reserva.online_payments.order_by("created_at")
        return _json(serializers.reservation(reserva, pagos=pagos))


class ReservationCancelView(ApiView):
    def post(self, request, number):
        reserva = services.reservation_for(self.api_client, number)
        reserva = services.cancel(client=self.api_client, reservation=reserva)
        return _json(serializers.reservation(reserva))


class PaymentLinkView(ApiView):
    def post(self, request, number):
        reserva = services.reservation_for(self.api_client, number)
        datos = self.valid(PaymentRequestForm(self.body()))
        pago = services.payment_link(
            client=self.api_client,
            reservation=reserva,
            provider=datos["provider"],
            purpose=datos["purpose"],
            amount=datos.get("amount"),
        )
        return _json(serializers.payment(pago), status=201)
