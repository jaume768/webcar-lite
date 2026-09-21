"""Comunicaciones a SES.Hospedajes (RD 933/2021)."""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class SesStatus(models.TextChoices):
    INCOMPLETE = "incomplete", _("Faltan datos")
    READY = "ready", _("Lista para enviar")
    SENT = "sent", _("Enviada")
    ACCEPTED = "accepted", _("Aceptada")
    REJECTED = "rejected", _("Rechazada")
    ERROR = "error", _("Error de envío")


class SesSubmissionQuerySet(models.QuerySet):
    def for_user(self, user):
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(reservation__pickup_office__in=user.offices.all())

    def pending(self):
        """Lo que aun no ha llegado al Ministerio."""
        return self.exclude(status__in=(SesStatus.SENT, SesStatus.ACCEPTED))


class SesSubmission(TimeStampedModel):
    """El parte de un contrato de alquiler. Uno por reserva.

    Se prepara al entregar el coche y se guarda tal cual se envio (datos y
    XML), con la respuesta del Ministerio: es la prueba de que se comunico.
    """

    reservation = models.OneToOneField(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="ses_submission",
    )
    status = models.CharField(
        _("estado"), max_length=20, choices=SesStatus.choices, default=SesStatus.INCOMPLETE
    )
    payload = models.JSONField(_("datos"), default=dict)
    xml = models.TextField(_("XML"), blank=True)
    missing = models.JSONField(_("datos que faltan"), default=list, blank=True)
    deadline_at = models.DateTimeField(_("plazo"), null=True, blank=True)

    attempts = models.PositiveSmallIntegerField(_("intentos"), default=0)
    last_attempt_at = models.DateTimeField(_("ultimo intento"), null=True, blank=True)
    sent_at = models.DateTimeField(_("enviada el"), null=True, blank=True)
    simulated = models.BooleanField(
        _("simulada"),
        default=False,
        help_text=_("Validada y guardada sin enviar: el envio real esta desactivado."),
    )
    lot_code = models.CharField(_("codigo de lote"), max_length=60, blank=True)
    response = models.TextField(_("respuesta del Ministerio"), blank=True)

    objects = SesSubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = _("comunicacion a SES.Hospedajes")
        verbose_name_plural = _("comunicaciones a SES.Hospedajes")
        ordering = ["deadline_at", "-created_at"]
        default_permissions = ("view", "change")

    def __str__(self):
        return f"SES {self.reservation.number}"

    @property
    def is_overdue(self) -> bool:
        return (
            self.deadline_at is not None
            and self.status not in (SesStatus.SENT, SesStatus.ACCEPTED)
            and self.deadline_at < timezone.now()
        )
