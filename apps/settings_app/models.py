"""Configuracion de la empresa, condiciones generales versionadas y politicas."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel


class SettingsPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    """Ancla del permiso de area, que no cuelga de ningun modelo."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de configuracion")
        permissions = [
            ("access_settings", _("Puede acceder a la configuracion de la empresa")),
        ]


validar_cif = RegexValidator(
    regex=r"^[A-Za-z0-9]{8,12}$",
    message=_("El CIF o NIF son entre 8 y 12 letras o numeros, sin espacios ni guiones."),
)


class CompanySettings(TimeStampedModel):
    """Los datos de la empresa que salen en contratos y facturas.

    Hay una sola fila. No es un `Site` de Django ni una variable de entorno
    porque estos datos los edita el cliente desde la aplicacion, y una factura
    de hace un ano tiene que poder decir la direccion que tenia entonces (por
    eso la factura los **copia** al emitirse; ver la regla de dominio).
    """

    legal_name = models.CharField(_("razon social"), max_length=160)
    trade_name = models.CharField(_("nombre comercial"), max_length=160, blank=True, default="")
    tax_id = models.CharField(_("CIF / NIF"), max_length=12, validators=[validar_cif])

    address = models.CharField(_("direccion"), max_length=200)
    city = models.CharField(_("localidad"), max_length=100)
    province = models.CharField(_("provincia"), max_length=100, blank=True, default="")
    postal_code = models.CharField(_("codigo postal"), max_length=10)
    country = models.CharField(_("pais"), max_length=2, default="ES")

    phone = models.CharField(_("telefono"), max_length=20, blank=True, default="")
    email = models.EmailField(_("correo electronico"), blank=True, default="")
    website = models.URLField(_("web"), blank=True, default="")

    logo = models.ImageField(
        _("logotipo"),
        upload_to="empresa/",
        blank=True,
        help_text=_("Sale en la cabecera del contrato y de la factura."),
    )

    registry_note = models.TextField(
        _("nota registral"),
        blank=True,
        default="",
        help_text=_("Inscripcion en el registro mercantil, si procede. Va en el pie."),
    )

    class Meta:
        verbose_name = _("datos de la empresa")
        verbose_name_plural = _("datos de la empresa")

    def __str__(self):
        return self.legal_name

    def save(self, *args, **kwargs):
        # Fila unica: cualquier guardado escribe siempre la misma.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("Los datos de la empresa no se borran, se editan."))

    @classmethod
    def load(cls) -> "CompanySettings":
        """Los datos de la empresa, creando la fila vacia si aun no existe."""
        objeto, _creado = cls.objects.get_or_create(
            pk=1,
            defaults={
                "legal_name": _("Configura los datos de la empresa"),
                "tax_id": "00000000",
                "address": "",
                "city": "",
                "postal_code": "",
            },
        )
        return objeto

    @property
    def display_name(self) -> str:
        return self.trade_name or self.legal_name

    @property
    def full_address(self) -> str:
        partes = [self.address, f"{self.postal_code} {self.city}".strip(), self.province]
        return ", ".join(parte for parte in partes if parte)


class TermsVersion(TimeStampedModel):
    """Una version de las condiciones generales del alquiler.

    Se versionan porque el contrato tiene que poder decir, dentro de tres anos,
    exactamente que firmo el cliente. Una version publicada **no se edita**: si
    hay que cambiar algo, se publica otra, y los contratos ya firmados siguen
    apuntando a la suya.
    """

    version = models.PositiveIntegerField(_("version"), unique=True, editable=False)
    title = models.CharField(_("titulo"), max_length=160, default=_("Condiciones generales"))
    body = models.TextField(
        _("texto"),
        help_text=_("Una clausula por parrafo. Se imprime tal cual al final del contrato."),
    )

    published_at = models.DateTimeField(_("publicada el"), null=True, blank=True)
    is_current = models.BooleanField(
        _("vigente"),
        default=False,
        db_index=True,
        help_text=_("La que se aplica a los contratos nuevos. Solo puede haber una."),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("redactada por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="terms_versions",
    )

    class Meta:
        verbose_name = _("version de las condiciones")
        verbose_name_plural = _("versiones de las condiciones")
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"],
                condition=models.Q(is_current=True),
                name="settings_una_sola_version_vigente",
            ),
        ]

    def __str__(self):
        return f"{self.title} v{self.version}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    @property
    def clauses(self) -> list[str]:
        """El texto partido en clausulas, para imprimirlo numerado."""
        return [linea.strip() for linea in self.body.splitlines() if linea.strip()]

    def save(self, *args, **kwargs):
        if self.published_at is not None and self.pk is not None:
            original = TermsVersion.objects.filter(pk=self.pk).first()
            # Solo se permite mover la marca de vigencia; el texto no.
            if (
                original is not None
                and original.published_at is not None
                and (original.body != self.body or original.title != self.title)
            ):
                raise ValidationError(
                    _(
                        "Las condiciones v%(version)s ya estan publicadas y no se "
                        "pueden editar. Publica una version nueva."
                    )
                    % {"version": original.version}
                )
        if not self.version:
            ultimo = TermsVersion.objects.order_by("-version").values_list("version", flat=True)
            self.version = (ultimo.first() or 0) + 1
        super().save(*args, **kwargs)

    @classmethod
    def current(cls) -> "TermsVersion | None":
        return cls.objects.filter(is_current=True).first()

    def publish(self, *, actor=None) -> "TermsVersion":
        """Publica esta version y retira la anterior."""
        from django.db import transaction

        with transaction.atomic():
            TermsVersion.objects.filter(is_current=True).exclude(pk=self.pk).update(
                is_current=False
            )
            self.published_at = self.published_at or timezone.now()
            self.is_current = True
            if actor is not None and self.created_by_id is None:
                self.created_by = actor
            super().save()
        return self


class Policy(TimeStampedModel, ActivableModel):
    """Una politica de la empresa: cancelacion, combustible, fianza, danos...

    Las marcadas para factura se imprimen al pie de las facturas. La factura
    copia su texto al emitirse: cambiar una politica manana no reescribe las
    facturas de ayer.
    """

    title = models.CharField(_("titulo"), max_length=120)
    body = models.TextField(_("texto"))
    show_on_invoice = models.BooleanField(
        _("sale en las facturas"),
        default=True,
        help_text=_("Se imprime al pie de las facturas que se emitan a partir de ahora."),
    )
    sort_order = models.PositiveSmallIntegerField(
        _("orden"), default=0, help_text=_("Las de numero mas bajo salen primero.")
    )

    class Meta:
        verbose_name = _("politica")
        verbose_name_plural = _("politicas")
        ordering = ["sort_order", "title"]
        # Se desactivan, no se borran: igual que el resto de maestros.
        default_permissions = ("view", "add", "change")

    def __str__(self):
        return self.title


def policies_for_invoice() -> list[dict]:
    """Texto de las politicas que se imprimen en una factura, listo para copiar."""
    return [
        {"title": politica.title, "body": politica.body}
        for politica in Policy.objects.filter(is_active=True, show_on_invoice=True)
    ]
