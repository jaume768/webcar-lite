"""Pasarelas de pago online. Cada una expone lo mismo y nada mas.

- `start(pago, urls)`: lo que hay que hacer para que el cliente pague (una URL a
  la que redirigir o un formulario que enviar).
- verificar la notificacion firmada que manda la pasarela.
- liberar o cobrar una preautorizacion (la fianza).

Ninguna registra cobros: eso lo hace `billing.online` con el servicio de
siempre, cuando la pasarela confirma con una notificacion firmada.
"""

from django.utils.translation import gettext_lazy as _


class GatewayError(Exception):
    """La pasarela no esta configurada o ha rechazado la operacion."""


PROVEEDORES = {
    "stripe": _("Stripe (tarjeta, Apple Pay, Google Pay)"),
    "redsys": _("Redsys (TPV virtual del banco)"),
}


def enabled_providers() -> list[tuple[str, str]]:
    """Solo las que tienen credenciales: no se ofrece lo que no funcionaria."""
    from django.conf import settings

    activas = []
    if settings.STRIPE_SECRET_KEY:
        activas.append(("stripe", PROVEEDORES["stripe"]))
    if settings.REDSYS_MERCHANT_CODE and settings.REDSYS_SECRET_KEY:
        activas.append(("redsys", PROVEEDORES["redsys"]))
    return activas
