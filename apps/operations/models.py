"""Operativa de mostrador: entrega, devolucion y partes de danos.

El check-in y el check-out son actas: dejan escrito en que estado salio y en
que estado volvio el coche. Por eso guardan kilometros, combustible y danos con
fotos, y por eso no se borran.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class FuelLevel(models.IntegerChoices):
    """Nivel del deposito en octavos, que es como se lee una aguja."""

    EMPTY = 0, _("Vacio")
    EIGHTH = 12, _("1/8")
    QUARTER = 25, _("1/4")
    THREE_EIGHTHS = 38, _("3/8")
    HALF = 50, _("1/2")
    FIVE_EIGHTHS = 62, _("5/8")
    THREE_QUARTERS = 75, _("3/4")
    SEVEN_EIGHTHS = 88, _("7/8")
    FULL = 100, _("Lleno")


class DamageZone(models.TextChoices):
    """Zonas del croquis. Son las que se pinchan en el dibujo del coche."""

    FRONT_BUMPER = "front_bumper", _("Paragolpes delantero")
    BONNET = "bonnet", _("Capo")
    WINDSCREEN = "windscreen", _("Parabrisas")
    ROOF = "roof", _("Techo")
    FRONT_LEFT = "front_left", _("Lateral delantero izquierdo")
    REAR_LEFT = "rear_left", _("Lateral trasero izquierdo")
    FRONT_RIGHT = "front_right", _("Lateral delantero derecho")
    REAR_RIGHT = "rear_right", _("Lateral trasero derecho")
    REAR_BUMPER = "rear_bumper", _("Paragolpes trasero")
    BOOT = "boot", _("Porton trasero")
    WHEELS = "wheels", _("Ruedas y llantas")
    INTERIOR = "interior", _("Interior")


class DamageType(models.TextChoices):
    SCRATCH = "scratch", _("Aranazo")
    DENT = "dent", _("Golpe o bollo")
    BROKEN = "broken", _("Rotura")
    MISSING = "missing", _("Falta una pieza")
    STAIN = "stain", _("Mancha")
    OTHER = "other", _("Otros")


class DamageSeverity(models.IntegerChoices):
    LIGHT = 1, _("Leve")
    MODERATE = 2, _("Moderado")
    SERIOUS = 3, _("Grave")


class CheckIn(TimeStampedModel):
    """Acta de entrega: como sale el coche."""

    reservation = models.OneToOneField(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="check_in",
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        verbose_name=_("vehiculo"),
        on_delete=models.PROTECT,
        related_name="check_ins",
    )

    actual_datetime = models.DateTimeField(_("fecha y hora de entrega"), default=timezone.now)
    mileage = models.PositiveIntegerField(_("kilometros"))
    fuel_level = models.PositiveSmallIntegerField(
        _("combustible"),
        choices=FuelLevel.choices,
        default=FuelLevel.FULL,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Nivel del deposito al entregar, en octavos."),
    )

    observations = models.TextField(_("observaciones"), blank=True, default="")

    # Verificacion de documentos: sin las dos cosas no sale el coche.
    licence_verified = models.BooleanField(_("carnet comprobado"), default=False)
    id_verified = models.BooleanField(_("DNI o pasaporte comprobado"), default=False)

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("empleado"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="check_ins",
    )

    #: Reservado para la firma del cliente. En v1 no hay firma digital: el
    #: contrato se firma en papel y aqui solo queda el hueco preparado.
    customer_signature = models.TextField(_("firma del cliente"), blank=True, default="")
    signed_at = models.DateTimeField(_("firmado el"), null=True, blank=True)

    class Meta:
        verbose_name = _("entrega")
        verbose_name_plural = _("entregas")
        ordering = ["-actual_datetime"]

    def __str__(self):
        return f"{_('Entrega')} {self.reservation_id}"

    @property
    def documents_verified(self) -> bool:
        return self.licence_verified and self.id_verified


class CheckOut(TimeStampedModel):
    """Acta de devolucion: como vuelve el coche y que se cobra por ello."""

    reservation = models.OneToOneField(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="check_out",
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        verbose_name=_("vehiculo"),
        on_delete=models.PROTECT,
        related_name="check_outs",
    )
    return_office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina de devolucion"),
        on_delete=models.PROTECT,
        related_name="check_outs",
        help_text=_("Donde se devuelve de verdad. Puede no ser la prevista."),
    )

    actual_datetime = models.DateTimeField(_("fecha y hora de devolucion"), default=timezone.now)
    mileage = models.PositiveIntegerField(_("kilometros"))
    fuel_level = models.PositiveSmallIntegerField(
        _("combustible"),
        choices=FuelLevel.choices,
        default=FuelLevel.FULL,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    observations = models.TextField(_("observaciones"), blank=True, default="")
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("empleado"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="check_outs",
    )

    class Meta:
        verbose_name = _("devolucion")
        verbose_name_plural = _("devoluciones")
        ordering = ["-actual_datetime"]

    def __str__(self):
        return f"{_('Devolucion')} {self.reservation_id}"

    @property
    def km_driven(self) -> int:
        entrega = getattr(self.reservation, "check_in", None)
        if entrega is None:
            return 0
        return max(self.mileage - entrega.mileage, 0)


class Damage(TimeStampedModel):
    """Un dano concreto del coche, localizado en el croquis.

    Los que se anotan en la entrega son **preexistentes**: cuando el coche
    vuelve aparecen ya marcados, para que nadie los cobre dos veces.
    """

    reservation = models.ForeignKey(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="damages",
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        verbose_name=_("vehiculo"),
        on_delete=models.PROTECT,
        related_name="damages",
    )

    zone = models.CharField(_("zona"), max_length=20, choices=DamageZone.choices)
    damage_type = models.CharField(_("tipo"), max_length=20, choices=DamageType.choices)
    severity = models.PositiveSmallIntegerField(
        _("gravedad"), choices=DamageSeverity.choices, default=DamageSeverity.LIGHT
    )
    description = models.TextField(_("descripcion"), blank=True, default="")

    is_preexisting = models.BooleanField(
        _("preexistente"),
        default=False,
        db_index=True,
        help_text=_("Ya estaba cuando se entrego el coche. No se le cobra al cliente."),
    )
    estimated_cost = models.DecimalField(
        _("coste estimado"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    charge_to_customer = models.BooleanField(
        _("se repercute al cliente"),
        default=False,
        help_text=_("Un dano preexistente no se repercute nunca."),
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("anotado por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="damages_recorded",
    )

    class Meta:
        verbose_name = _("dano")
        verbose_name_plural = _("danos")
        ordering = ["zone", "-created_at"]
        indexes = [
            models.Index(fields=["vehicle", "is_preexisting"], name="operations_dano_vehiculo"),
        ]

    def __str__(self):
        return f"{self.get_zone_display()}: {self.get_damage_type_display()}"


class DamagePhoto(TimeStampedModel):
    """Foto de un dano. Es la prueba: sin ella, un parte es la palabra de uno."""

    damage = models.ForeignKey(
        Damage,
        verbose_name=_("dano"),
        on_delete=models.CASCADE,
        related_name="photos",
    )
    image = models.ImageField(_("foto"), upload_to="danos/%Y/%m/")
    caption = models.CharField(_("pie de foto"), max_length=160, blank=True, default="")

    class Meta:
        verbose_name = _("foto del dano")
        verbose_name_plural = _("fotos del dano")
        ordering = ["created_at"]

    def __str__(self):
        return self.caption or f"{_('Foto')} {self.pk}"
