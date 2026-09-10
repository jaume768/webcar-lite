"""Contrato de alquiler: el PDF que firma el cliente en el mostrador."""

from django.conf import settings
from django.core.files.storage import storages
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def contrato_privado():
    """El contrato lleva datos personales: nunca en el almacen publico."""
    return storages["private"]


class ContractStatus(models.TextChoices):
    PENDING = "pending", _("Generandose")
    READY = "ready", _("Listo")
    FAILED = "failed", _("Fallo")


class Contract(TimeStampedModel):
    """Un contrato generado para una reserva.

    Se puede regenerar (cambian los conductores, se corrige una fecha): cada
    generacion es una fila nueva y la ultima es la vigente. Las anteriores se
    conservan porque puede haberse impreso y firmado alguna.
    """

    reservation = models.ForeignKey(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="contracts",
    )
    terms_version = models.ForeignKey(
        "settings_app.TermsVersion",
        verbose_name=_("condiciones aplicadas"),
        on_delete=models.PROTECT,
        related_name="contracts",
        help_text=_("Que condiciones generales se imprimieron en este contrato."),
    )

    status = models.CharField(
        _("estado"),
        max_length=20,
        choices=ContractStatus.choices,
        default=ContractStatus.PENDING,
        db_index=True,
    )
    file = models.FileField(
        _("fichero"),
        upload_to="contratos/%Y/%m/",
        storage=contrato_privado,
        blank=True,
        help_text=_("PDF firmado a mano en mostrador. Sin URL publica."),
    )
    error = models.TextField(_("error"), blank=True, default="")

    generated_at = models.DateTimeField(_("generado el"), null=True, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("generado por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contracts_generated",
    )

    class Meta:
        verbose_name = _("contrato")
        verbose_name_plural = _("contratos")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["reservation", "-created_at"], name="contracts_reserva"),
        ]

    def __str__(self):
        return f"{_('Contrato')} {self.reservation_id}"

    @property
    def is_ready(self) -> bool:
        return self.status == ContractStatus.READY and bool(self.file)

    @property
    def filename(self) -> str:
        return f"contrato-{self.reservation.number}.pdf"
