"""Registro de los correos al cliente: que se envio, a quien y como acabo."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class EmailKind(models.TextChoices):
    CONFIRMATION = "confirmation", _("Confirmación de reserva")
    REMINDER = "reminder", _("Recordatorio e instrucciones de recogida")
    CONTRACT = "contract", _("Contrato")
    RETURN = "return", _("Devolución")
    INVOICE = "invoice", _("Factura")
    PAYMENT_LINK = "payment_link", _("Enlace de pago")
    PAYMENT_RECEIVED = "payment_received", _("Pago recibido")


class EmailStatus(models.TextChoices):
    QUEUED = "queued", _("En cola")
    SENT = "sent", _("Enviado")
    FAILED = "failed", _("Error")
    SKIPPED = "skipped", _("No enviado")


class EmailLogQuerySet(models.QuerySet):
    def for_user(self, user):
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(reservation__pickup_office__in=user.offices.all())


class EmailLog(TimeStampedModel):
    kind = models.CharField(_("tipo"), max_length=30, choices=EmailKind.choices)
    status = models.CharField(
        _("estado"), max_length=20, choices=EmailStatus.choices, default=EmailStatus.QUEUED
    )
    reservation = models.ForeignKey(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emails",
    )
    to_email = models.EmailField(_("para"), blank=True)
    language = models.CharField(_("idioma"), max_length=5, default="es")
    subject = models.CharField(_("asunto"), max_length=200, blank=True)
    #: Datos extra para montar el correo (el pago online, la factura...).
    context = models.JSONField(_("contexto"), default=dict, blank=True)
    error = models.TextField(_("error"), blank=True)
    provider_id = models.CharField(_("id en el proveedor"), max_length=120, blank=True)
    attempts = models.PositiveSmallIntegerField(_("intentos"), default=0)
    sent_at = models.DateTimeField(_("enviado el"), null=True, blank=True)

    objects = EmailLogQuerySet.as_manager()

    class Meta:
        verbose_name = _("correo enviado")
        verbose_name_plural = _("correos enviados")
        ordering = ["-created_at"]
        default_permissions = ("view",)
        indexes = [models.Index(fields=["reservation", "kind"], name="notif_reserva_tipo")]

    def __str__(self):
        return f"{self.get_kind_display()} → {self.to_email or '—'}"

    @property
    def badge_status(self) -> str:
        return f"email_{self.status}"
