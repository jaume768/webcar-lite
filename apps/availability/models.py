"""Cerrojos de disponibilidad.

La app no tiene datos propios: la disponibilidad se calcula a partir de la
flota, los bloqueos y las reservas. Lo unico que vive aqui es la fila sobre la
que se serializan las reservas de una misma categoria, y el ancla de permisos.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class AvailabilityPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    """Ancla de permisos de la app. No crea tabla."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de disponibilidad")
        permissions = [
            ("override_availability", _("Puede forzar una reserva sin disponibilidad")),
        ]


class CategoryLock(models.Model):
    """Fila sobre la que se serializa el recuento de capacidad.

    Dos empleados que reservan a la vez el ultimo coche de una categoria tienen
    que ponerse en fila para contar: si cuentan a la vez, los dos ven un hueco
    libre y los dos venden. Antes de contar, `reserve_capacity()` bloquea esta
    fila con `SELECT ... FOR UPDATE`.

    Por que una fila propia y no `select_for_update()` sobre los vehiculos, en
    docs/decisiones/ADR-001-disponibilidad.md.
    """

    category = models.ForeignKey(
        "fleet.VehicleCategory",
        verbose_name=_("categoria"),
        on_delete=models.CASCADE,
        related_name="locks",
    )
    #: Ambito sobre el que se cuenta: "pool:3" o "office:7" cuando la oficina no
    #: pertenece a ningun grupo. Es texto y no dos claves ajenas anulables para
    #: que la unicidad sea una restriccion normal, sin semantica de NULL.
    scope_key = models.CharField(_("ambito"), max_length=40)

    class Meta:
        verbose_name = _("cerrojo de categoria")
        verbose_name_plural = _("cerrojos de categoria")
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope_key"],
                name="availability_cerrojo_unico_por_ambito",
            ),
        ]

    def __str__(self):
        return f"{self.category_id}@{self.scope_key}"
