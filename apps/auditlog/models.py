"""Registro de auditoria transversal.

Una fila por hecho relevante: quien, que, sobre que, cuando y desde donde. Es
de solo alta: una auditoria que se puede editar no audita nada. Por eso
`save()` sobre una fila existente y `delete()` revientan, igual que un cobro.

No lleva claves ajenas a los objetos auditados (van como tipo + id): el
registro tiene que sobrevivir a cualquier cosa que le pase al objeto, y una FK
obligaria a elegir entre borrarlo en cascada o bloquear el borrado.
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditImmutable(Exception):
    """Se ha intentado editar o borrar un apunte de auditoria."""


class AuditAction(models.TextChoices):
    CREATE = "create", _("Alta")
    UPDATE = "update", _("Modificación")
    STATUS = "status", _("Cambio de estado")
    CANCEL = "cancel", _("Cancelación")
    PRICE = "price", _("Cambio de precio")
    VEHICLE = "vehicle", _("Asignación de vehículo")
    PAYMENT = "payment", _("Cobro")
    INVOICE = "invoice", _("Factura")
    CHECK_IN = "check_in", _("Entrega")
    CHECK_OUT = "check_out", _("Devolución")
    LOGIN = "login", _("Acceso")
    LOGIN_FAILED = "login_failed", _("Acceso fallido")
    COMPLIANCE = "compliance", _("SES.Hospedajes")
    EMAIL = "email", _("Correo")
    ONLINE_PAYMENT = "online_payment", _("Pago online")
    MAINTENANCE = "maintenance", _("Mantenimiento")
    FINE = "fine", _("Multa")


class AuditLogQuerySet(models.QuerySet):
    def for_user(self, user):
        """Scope de oficina: lo que no tiene oficina solo lo ve un superusuario."""
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(office_id__in=user.offices.values("pk"))

    def update(self, **kwargs):
        raise AuditImmutable("La auditoria no se modifica en bloque.")

    def delete(self):
        raise AuditImmutable("La auditoria no se borra en bloque.")


class AuditLog(models.Model):
    created_at = models.DateTimeField(_("cuando"), auto_now_add=True, db_index=True)
    action = models.CharField(_("accion"), max_length=20, choices=AuditAction.choices)
    message = models.CharField(_("que paso"), max_length=255)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("usuario"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    #: Copia del nombre: sigue diciendo quien fue aunque el usuario cambie.
    actor_repr = models.CharField(_("usuario (copia)"), max_length=160, blank=True)

    content_type = models.ForeignKey(
        ContentType, verbose_name=_("tipo"), on_delete=models.PROTECT, null=True, blank=True
    )
    object_id = models.CharField(_("id del objeto"), max_length=40, blank=True)
    object_repr = models.CharField(_("objeto"), max_length=200, blank=True)

    #: Reserva relacionada, para el historial de la ficha. Sin FK a proposito.
    reservation_id = models.BigIntegerField(_("reserva"), null=True, blank=True, db_index=True)
    office_id = models.BigIntegerField(_("oficina"), null=True, blank=True, db_index=True)

    changes = models.JSONField(_("cambios"), default=dict, blank=True)
    ip = models.GenericIPAddressField(_("IP"), null=True, blank=True)
    user_agent = models.CharField(_("navegador"), max_length=255, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        verbose_name = _("apunte de auditoria")
        verbose_name_plural = _("auditoria")
        ordering = ["-created_at", "-id"]
        default_permissions = ("view",)
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="auditlog_objeto"),
            models.Index(fields=["action", "created_at"], name="auditlog_accion_fecha"),
        ]

    def __str__(self):
        return f"{self.get_action_display()}: {self.message}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AuditImmutable("Un apunte de auditoria no se modifica.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutable("Un apunte de auditoria no se borra.")
