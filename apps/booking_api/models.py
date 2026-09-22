"""API de reservas para la web del cliente.

Cada web que reserva contra el sistema es un `ApiClient` con su clave. La
clave no se guarda: se guarda su huella (SHA-256) y un prefijo para encontrarla.
Detras de cada cliente hay un usuario tecnico, y es ese usuario el que firma las
reservas, pide los enlaces de pago y aparece en la auditoria. Asi la API no
tiene permisos propios ni un scope de oficina paralelo: usa los de siempre.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel


class ApiClient(TimeStampedModel, ActivableModel):
    name = models.CharField(_("nombre"), max_length=120)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("usuario tecnico"),
        on_delete=models.PROTECT,
        related_name="api_client",
        help_text=_("Sus oficinas son las que puede vender la web; su rol, lo que puede hacer."),
    )
    key_prefix = models.CharField(_("prefijo de la clave"), max_length=12, unique=True)
    key_hash = models.CharField(_("huella de la clave"), max_length=64)
    last_used_at = models.DateTimeField(_("ultimo uso"), null=True, blank=True)

    class Meta:
        verbose_name = _("cliente de la API")
        verbose_name_plural = _("clientes de la API")
        ordering = ["name"]

    def __str__(self):
        return self.name


class ApiReservation(TimeStampedModel):
    """Reserva que entro por la API, con la clave de idempotencia de la peticion.

    Es tambien lo que limita a un cliente de la API a leer y cancelar solo lo
    que el mismo creo.
    """

    api_client = models.ForeignKey(
        ApiClient,
        verbose_name=_("cliente de la API"),
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    reservation = models.OneToOneField(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="api_origin",
    )
    idempotency_key = models.CharField(_("clave de idempotencia"), max_length=80)
    request_hash = models.CharField(_("huella de la peticion"), max_length=64)
    external_ref = models.CharField(_("referencia en la web"), max_length=80, blank=True)

    class Meta:
        verbose_name = _("reserva por API")
        verbose_name_plural = _("reservas por API")
        constraints = [
            models.UniqueConstraint(
                fields=["api_client", "idempotency_key"], name="booking_api_idempotencia"
            )
        ]

    def __str__(self):
        return f"{self.api_client} · {self.idempotency_key}"
