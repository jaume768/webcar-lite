"""Aislamiento por oficina.

Regla del sistema: toda consulta de datos operativos pasa por aqui. Si un
usuario solo tiene la oficina centro, no puede leer ni escribir nada de la oficina norte, ni por URL
ni por formulario.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class OfficeScopedQuerySet(models.QuerySet):
    """Queryset que sabe recortarse a las oficinas de un usuario."""

    def for_user(self, user):
        """Filtra a lo que el usuario puede ver.

        Sin usuario, anonimo o desactivado: nada. Superusuario: todo. El resto,
        solo sus oficinas. Devolver el queryset entero por descuido dejaria el
        sistema abierto, asi que el caso por defecto es el vacio.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        if not user.is_active:
            return self.none()
        if user.is_superuser:
            return self

        campo = self.model.OFFICE_FIELD
        return self.filter(**{f"{campo}__in": user.offices.all()})

    def for_office(self, office):
        """Recorta a una oficina concreta (la activa de la sesion)."""
        if office is None:
            return self.none()
        return self.filter(**{self.model.OFFICE_FIELD: office})


class OfficeScopedManager(models.Manager.from_queryset(OfficeScopedQuerySet)):
    """Manager por defecto de los modelos con oficina."""


class OfficeScopedModel(models.Model):
    """Modelo que pertenece a una oficina.

    Aporta el campo y el manager con scope. Cualquier modelo con datos
    operativos hereda de aqui; si el campo se llama de otra forma, se redefine
    `OFFICE_FIELD`.
    """

    #: Ruta al campo de oficina, tal como la entiende `filter()`.
    OFFICE_FIELD = "office"

    office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina"),
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
    )

    objects = OfficeScopedManager()

    class Meta:
        abstract = True
