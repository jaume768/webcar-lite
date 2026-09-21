"""Stripe Checkout con la API REST (sin SDK).

La fianza va como preautorizacion (`capture_method=manual`): Stripe retiene el
importe en la tarjeta y no lo cobra hasta que se captura. Una retencion de
tarjeta caduca a los 7 dias, asi que sirve para alquileres cortos; para los
largos, mejor cobrar la fianza y devolverla.
"""

import hashlib
import hmac
import time
from decimal import Decimal

from django.conf import settings

from apps.core.http import HttpError, post_form

from . import GatewayError

API = "https://api.stripe.com/v1"
#: Margen para aceptar una notificacion firmada (contra reenvios antiguos).
TOLERANCIA_SEGUNDOS = 300


def _cabeceras() -> dict:
    if not settings.STRIPE_SECRET_KEY:
        raise GatewayError("Stripe no esta configurado (STRIPE_SECRET_KEY).")
    return {"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"}


def _post(ruta: str, campos) -> dict:
    try:
        respuesta = post_form(f"{API}{ruta}", campos, headers=_cabeceras())
    except HttpError as exc:
        raise GatewayError(f"Sin conexion con Stripe: {exc}") from exc
    datos = respuesta.json() or {}
    if not respuesta.ok:
        mensaje = (datos.get("error") or {}).get("message") or respuesta.body[:300]
        raise GatewayError(f"Stripe: {mensaje}")
    return datos


def centimos(importe: Decimal) -> int:
    return int((Decimal(importe) * 100).quantize(Decimal("1")))


def create_checkout(
    *,
    token: str,
    amount: Decimal,
    description: str,
    success_url: str,
    cancel_url: str,
    email: str = "",
    locale: str = "es",
    hold: bool = False,
    expires_at: int | None = None,
) -> dict:
    """Crea la sesion de pago y devuelve `{"id", "url"}`."""
    campos = [
        ("mode", "payment"),
        ("line_items[0][quantity]", "1"),
        ("line_items[0][price_data][currency]", settings.DEFAULT_CURRENCY.lower()),
        ("line_items[0][price_data][unit_amount]", str(centimos(amount))),
        ("line_items[0][price_data][product_data][name]", description[:250]),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("client_reference_id", token),
        ("metadata[token]", token),
        ("payment_intent_data[metadata][token]", token),
        ("locale", locale if locale in ("es", "en", "de", "fr") else "auto"),
    ]
    if hold:
        campos.append(("payment_intent_data[capture_method]", "manual"))
    if email:
        campos.append(("customer_email", email))
    if expires_at:
        campos.append(("expires_at", str(expires_at)))
    sesion = _post("/checkout/sessions", campos)
    return {"id": sesion["id"], "url": sesion["url"]}


def capture(payment_intent: str, amount: Decimal) -> dict:
    """Cobra parte (o todo) de una preautorizacion. El resto se libera solo."""
    return _post(
        f"/payment_intents/{payment_intent}/capture",
        [("amount_to_capture", str(centimos(amount)))],
    )


def release(payment_intent: str) -> dict:
    """Anula la preautorizacion: la tarjeta queda libre sin cobrar nada."""
    return _post(f"/payment_intents/{payment_intent}/cancel", [])


def verify_webhook(body: bytes, firma: str, *, secret: str | None = None, ahora=None) -> bool:
    """Comprueba la cabecera Stripe-Signature (`t=...,v1=...`) contra el cuerpo."""
    secreto = secret if secret is not None else settings.STRIPE_WEBHOOK_SECRET
    if not secreto or not firma:
        return False
    partes = [trozo.split("=", 1) for trozo in firma.split(",") if "=" in trozo]
    marca = next((valor for clave, valor in partes if clave == "t"), None)
    candidatas = [valor for clave, valor in partes if clave == "v1"]
    if marca is None or not candidatas:
        return False
    try:
        if abs((ahora or time.time()) - int(marca)) > TOLERANCIA_SEGUNDOS:
            return False
    except ValueError:
        return False
    esperada = hmac.new(secreto.encode(), f"{marca}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(esperada, candidata) for candidata in candidatas)
