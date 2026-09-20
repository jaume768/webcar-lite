"""Oficina: la unidad de aislamiento de datos de todo el sistema."""

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel

validar_codigo_postal = RegexValidator(
    regex=r"^\d{5}$",
    message=_("El codigo postal espanol tiene cinco digitos."),
)


class OfficePool(TimeStampedModel, ActivableModel):
    """Grupo de oficinas entre las que la flota se mueve libremente.

    Es lo que permite resolver el one-way: un coche recogido en el centro y
    devuelto en el aeropuerto no sale de la capacidad del grupo, asi que la
    disponibilidad se calcula sobre el pool y no sobre cada oficina por
    separado. Una oficina sin pool responde solo de su propia flota.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe un grupo con ese codigo.")},
        help_text=_("Identificador corto y estable."),
    )
    name = models.CharField(_("nombre"), max_length=120)
    description = models.TextField(_("descripcion"), blank=True)

    class Meta:
        verbose_name = _("grupo de oficinas")
        verbose_name_plural = _("grupos de oficinas")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Office(TimeStampedModel, ActivableModel):
    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe una oficina con ese codigo.")},
        help_text=_("Identificador corto y estable. Aparece en series de facturacion."),
    )
    name = models.CharField(_("nombre"), max_length=120)

    pool = models.ForeignKey(
        OfficePool,
        verbose_name=_("grupo de oficinas"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offices",
        help_text=_("Oficinas entre las que la flota se mueve sin restricciones."),
    )

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
