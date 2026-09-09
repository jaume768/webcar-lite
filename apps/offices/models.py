"""Oficina: la unidad de aislamiento de datos de todo el sistema."""

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel

validar_codigo_postal = RegexValidator(
    regex=r"^\d{5}$",
    message=_("El codigo postal espanol tiene cinco digitos."),
)


class Office(TimeStampedModel, ActivableModel):
    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        help_text=_("Identificador corto y estable. Aparece en series de facturacion."),
    )
    name = models.CharField(_("nombre"), max_length=120)

    address = models.CharField(_("direccion"), max_length=200, blank=True)
    city = models.CharField(_("localidad"), max_length=100, blank=True)
    province = models.CharField(_("provincia"), max_length=100, blank=True)
    postal_code = models.CharField(
        _("codigo postal"),
        max_length=5,
        blank=True,
        validators=[validar_codigo_postal],
    )
    country = models.CharField(_("pais"), max_length=2, default="ES")

    phone = models.CharField(_("telefono"), max_length=20, blank=True)
    email = models.EmailField(_("correo electronico"), blank=True)

    class Meta:
        verbose_name = _("oficina")
        verbose_name_plural = _("oficinas")
        ordering = ["name"]

    def __str__(self):
        return self.name
