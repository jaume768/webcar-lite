"""Tarifas, temporadas, extras, suplementos y descuentos.

Aqui vive **como se cobra**, no el calculo: el motor esta en
`pricing.services` y estos modelos son solo los datos que consume. Esa
separacion es lo que permite cambiar los tramos de una tarifa en la base de
datos y ver el precio cambiar sin tocar una linea de codigo.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel


class PricingPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    """Ancla de permisos de pricing.

    Existe para colgar de la app permisos cuyos modelos aun no estan (tarifas,
    temporadas). No crea tabla. Cuando lleguen esos modelos, sus permisos se
    declaran en el Meta que corresponda y una migracion de datos mueve estas
    filas: los codigos no cambian, asi que ni los roles ni las vistas se enteran.
    """

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de tarifas")
        permissions = [
            ("manage_rates", _("Puede gestionar tarifas, temporadas y extras")),
        ]


class CalculationType(models.TextChoices):
    """Como se multiplica el precio de un extra."""

    ONCE = "once", _("Precio unico")
    PER_DAY = "per_day", _("Por dia de alquiler")
    PER_RESERVATION = "per_reservation", _("Por reserva")


class Extra(TimeStampedModel, ActivableModel):
    """Suplemento que se puede anadir a una reserva.

    El tope (`max_amount`) es lo que hace util el cobro por dia: "5 EUR/dia,
    maximo 50 EUR" es la forma normal de vender un seguro o una silla, y sin el
    tope un alquiler de un mes acabaria cobrando 150 EUR de silla.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe un extra con ese codigo.")},
        help_text=_("Identificador corto y estable. Aparece en factura y contrato."),
    )
    name = models.CharField(_("nombre"), max_length=120)
    description = models.TextField(_("descripcion"), blank=True)

    calculation_type = models.CharField(
        _("forma de cobro"),
        max_length=20,
        choices=CalculationType.choices,
        default=CalculationType.PER_DAY,
    )
    price = models.DecimalField(
        _("precio"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Base imponible, sin impuesto."),
    )
    tax_rate = models.DecimalField(
        _("impuesto (%)"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("21.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    max_quantity = models.PositiveSmallIntegerField(
        _("cantidad maxima"),
        default=1,
        help_text=_("Cuantas unidades se pueden anadir a una misma reserva."),
    )
    max_amount = models.DecimalField(
        _("importe maximo"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Tope para los extras por dia. Vacio: sin tope."),
    )

    requires_driver_data = models.BooleanField(
        _("pide datos de conductor"),
        default=False,
        help_text=_("Para el segundo conductor: obliga a rellenar sus datos y su carnet."),
    )

    sort_order = models.PositiveSmallIntegerField(
        _("orden"),
        default=100,
        db_index=True,
        help_text=_("Menor primero. Fija el orden en el que se ofrecen."),
    )

    class Meta:
        verbose_name = _("extra")
        verbose_name_plural = _("extras")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    @property
    def has_cap(self) -> bool:
        return self.max_amount is not None


class Season(TimeStampedModel, ActivableModel):
    """Temporada con precios propios (alta, media, baja, puentes...).

    Las temporadas **pueden solaparse** a proposito: encima de "temporada alta"
    se pone "Semana Santa" con mas prioridad y no hay que recortar la primera.
    Cuando dos cubren la misma fecha gana la de mayor `priority`.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe una temporada con ese codigo.")},
    )
    name = models.CharField(_("nombre"), max_length=120)
    start_date = models.DateField(_("desde"))
    end_date = models.DateField(_("hasta"))
    priority = models.IntegerField(
        _("prioridad"),
        default=0,
        db_index=True,
        help_text=_("Mayor gana cuando dos temporadas cubren la misma fecha."),
    )

    class Meta:
        verbose_name = _("temporada")
        verbose_name_plural = _("temporadas")
        ordering = ["-priority", "start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="pricing_season_fin_posterior_al_inicio",
            )
        ]
        indexes = [models.Index(fields=["start_date", "end_date"], name="pricing_season_fechas")]

    def __str__(self):
        return self.name

    def covers(self, fecha) -> bool:
        return self.start_date <= fecha <= self.end_date


class Channel(models.TextChoices):
    """Por donde entra la reserva. La misma categoria se vende a otro precio."""

    COUNTER = "counter", _("Mostrador")
    WEB = "web", _("Web")
    PARTNER = "partner", _("Partner")


class TierMode(models.TextChoices):
    """Como se aplican los tramos de dias de una tarifa."""

    FLAT = "flat", _("Plano")
    PROGRESSIVE = "progressive", _("Progresivo")


class Rate(TimeStampedModel, ActivableModel):
    """Tarifa: el precio por dia de una categoria, con sus condiciones.

    Una tarifa no lleva precio: lleva **tramos** (`RateTier`). El precio del dia
    sale del tramo en el que cae la duracion, y eso es lo que permite que un
    alquiler largo salga mas barato por dia.

    `offices` vacio significa "todas las oficinas": es lo normal, y obliga a
    rellenar la lista solo cuando de verdad hay un precio distinto en un sitio.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=30,
        unique=True,
        error_messages={"unique": _("Ya existe una tarifa con ese codigo.")},
    )
    name = models.CharField(_("nombre"), max_length=120)
    channel = models.CharField(
        _("canal"),
        max_length=20,
        choices=Channel.choices,
        default=Channel.COUNTER,
        db_index=True,
    )

    categories = models.ManyToManyField(
        "fleet.VehicleCategory",
        verbose_name=_("categorias"),
        related_name="rates",
        help_text=_("Categorias a las que se aplica esta tarifa."),
    )
    offices = models.ManyToManyField(
        "offices.Office",
        verbose_name=_("oficinas"),
        related_name="rates",
        blank=True,
        help_text=_("Vacio: se aplica en todas las oficinas."),
    )
    season = models.ForeignKey(
        Season,
        verbose_name=_("temporada"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rates",
        help_text=_("Vacio: vale para cualquier fecha dentro de su vigencia."),
    )

    valid_from = models.DateField(_("vigente desde"), null=True, blank=True)
    valid_to = models.DateField(_("vigente hasta"), null=True, blank=True)
    priority = models.IntegerField(
        _("prioridad"),
        default=0,
        db_index=True,
        help_text=_("Mayor gana. A igual prioridad decide cual es mas especifica."),
    )

    tier_mode = models.CharField(
        _("modo de tramos"),
        max_length=20,
        choices=TierMode.choices,
        default=TierMode.FLAT,
        help_text=_(
            "Plano: todos los dias al precio del tramo que corresponde a la duracion. "
            "Progresivo: cada dia al precio de su propio tramo."
        ),
    )

    included_km_per_day = models.PositiveIntegerField(
        _("km incluidos por dia"),
        null=True,
        blank=True,
        help_text=_("Vacio: kilometraje ilimitado."),
    )
    extra_km_price = models.DecimalField(
        _("precio del km de mas"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    min_days = models.PositiveSmallIntegerField(_("dias minimos"), default=1)
    max_days = models.PositiveSmallIntegerField(
        _("dias maximos"),
        null=True,
        blank=True,
        help_text=_("Vacio: sin limite."),
    )

    class Meta:
        verbose_name = _("tarifa")
        verbose_name_plural = _("tarifas")
        ordering = ["-priority", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_from__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="pricing_rate_vigencia_coherente",
            ),
            models.CheckConstraint(
                condition=models.Q(max_days__isnull=True)
                | models.Q(max_days__gte=models.F("min_days")),
                name="pricing_rate_dias_coherentes",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def unlimited_km(self) -> bool:
        return self.included_km_per_day is None

    def covers_days(self, days: int) -> bool:
        return self.min_days <= days and (self.max_days is None or days <= self.max_days)

    def covers_date(self, fecha) -> bool:
        desde_ok = self.valid_from is None or self.valid_from <= fecha
        hasta_ok = self.valid_to is None or fecha <= self.valid_to
        return desde_ok and hasta_ok


class RateTier(models.Model):
    """Tramo de dias de una tarifa: de `min_days` a `max_days`, a X por dia.

    `max_days` vacio es el ultimo tramo, el de "15 dias o mas". Sin el, un
    alquiler de dos meses se quedaria sin precio.
    """

    rate = models.ForeignKey(
        Rate,
        verbose_name=_("tarifa"),
        on_delete=models.CASCADE,
        related_name="tiers",
    )
    min_days = models.PositiveSmallIntegerField(_("desde (dias)"))
    max_days = models.PositiveSmallIntegerField(
        _("hasta (dias)"),
        null=True,
        blank=True,
        help_text=_("Vacio: sin limite (el ultimo tramo)."),
    )
    price_per_day = models.DecimalField(
        _("precio por dia"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        verbose_name = _("tramo de tarifa")
        verbose_name_plural = _("tramos de tarifa")
        ordering = ["rate", "min_days"]
        constraints = [
            models.UniqueConstraint(
                fields=["rate", "min_days"],
                name="pricing_tier_inicio_unico",
                violation_error_message=_("Esa tarifa ya tiene un tramo que empieza ese dia."),
            ),
            models.CheckConstraint(
                condition=models.Q(max_days__isnull=True)
                | models.Q(max_days__gte=models.F("min_days")),
                name="pricing_tier_dias_coherentes",
            ),
        ]

    def __str__(self):
        if self.max_days is None:
            return f"{self.min_days}+ dias: {self.price_per_day}"
        return f"{self.min_days}-{self.max_days} dias: {self.price_per_day}"

    def covers(self, days: int) -> bool:
        return self.min_days <= days and (self.max_days is None or days <= self.max_days)

    def days_within(self, days: int) -> int:
        """Cuantos de los `days` caen dentro de este tramo (modo progresivo)."""
        if days < self.min_days:
            return 0
        hasta = days if self.max_days is None else min(days, self.max_days)
        return hasta - self.min_days + 1


class SupplementType(models.TextChoices):
    ONE_WAY = "one_way", _("Devolucion en otra oficina")
    YOUNG_DRIVER = "young_driver", _("Conductor joven")
    AFTER_HOURS = "after_hours", _("Fuera de horario")
    AIRPORT = "airport", _("Aeropuerto")


class AmountType(models.TextChoices):
    FIXED = "fixed", _("Importe fijo")
    PERCENT = "percent", _("Porcentaje sobre la base del alquiler")


class Supplement(TimeStampedModel, ActivableModel):
    """Cargo que se anade solo cuando se cumple su condicion.

    Cada tipo mira una cosa distinta, y esa es toda la logica que hay: el motor
    pregunta si aplica y no sabe nada mas.

    Un suplemento por dia (el tipico de conductor joven) se modela como
    **porcentaje**: la base del alquiler ya es proporcional a los dias, asi que
    un 15% sube igual que un fijo diario y ademas acompana al precio del tramo.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=30,
        unique=True,
        error_messages={"unique": _("Ya existe un suplemento con ese codigo.")},
    )
    name = models.CharField(_("nombre"), max_length=120)
    supplement_type = models.CharField(
        _("tipo"),
        max_length=20,
        choices=SupplementType.choices,
        db_index=True,
    )
    amount_type = models.CharField(
        _("forma de calculo"),
        max_length=20,
        choices=AmountType.choices,
        default=AmountType.FIXED,
    )
    amount = models.DecimalField(
        _("importe"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Euros si es fijo; porcentaje sobre la base si es porcentual."),
    )
    tax_rate = models.DecimalField(
        _("impuesto (%)"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("21.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    # --- Condiciones -------------------------------------------------------
    min_age = models.PositiveSmallIntegerField(
        _("edad minima"),
        null=True,
        blank=True,
        help_text=_("Conductor joven: desde que edad se cobra (normalmente 18)."),
    )
    max_age = models.PositiveSmallIntegerField(
        _("edad maxima"),
        null=True,
        blank=True,
        help_text=_("Conductor joven: hasta que edad se cobra (normalmente 24)."),
    )
    offices = models.ManyToManyField(
        "offices.Office",
        verbose_name=_("oficinas"),
        related_name="supplements",
        blank=True,
        help_text=_("Aeropuerto: oficinas que lo cobran. Vacio: ninguna."),
    )
    hours_from = models.TimeField(
        _("horario desde"),
        null=True,
        blank=True,
        help_text=_("Fuera de horario: apertura de la oficina."),
    )
    hours_to = models.TimeField(
        _("horario hasta"),
        null=True,
        blank=True,
        help_text=_("Fuera de horario: cierre de la oficina."),
    )

    sort_order = models.PositiveSmallIntegerField(_("orden"), default=100, db_index=True)

    class Meta:
        verbose_name = _("suplemento")
        verbose_name_plural = _("suplementos")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Discount(TimeStampedModel, ActivableModel):
    """Descuento, con o sin codigo.

    Sin codigo se aplica solo cuando se cumplen sus condiciones (una promocion
    de larga duracion, por ejemplo). Con codigo hay que teclearlo: nadie se
    lleva un descuento por accidente.
    """

    code = models.SlugField(
        _("codigo promocional"),
        max_length=30,
        blank=True,
        help_text=_("Vacio: se aplica solo, sin que nadie lo teclee."),
    )
    name = models.CharField(_("nombre"), max_length=120)
    amount_type = models.CharField(
        _("forma de calculo"),
        max_length=20,
        choices=AmountType.choices,
        default=AmountType.PERCENT,
    )
    amount = models.DecimalField(
        _("importe"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Euros si es fijo; porcentaje sobre la base del alquiler si es porcentual."),
    )

    valid_from = models.DateField(_("vigente desde"), null=True, blank=True)
    valid_to = models.DateField(_("vigente hasta"), null=True, blank=True)
    min_days = models.PositiveSmallIntegerField(
        _("dias minimos"),
        null=True,
        blank=True,
        help_text=_("Solo a partir de esta duracion."),
    )
    categories = models.ManyToManyField(
        "fleet.VehicleCategory",
        verbose_name=_("categorias"),
        related_name="discounts",
        blank=True,
        help_text=_("Vacio: cualquier categoria."),
    )

    class Meta:
        verbose_name = _("descuento")
        verbose_name_plural = _("descuentos")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=~models.Q(code=""),
                name="pricing_discount_codigo_unico",
                violation_error_message=_("Ya existe un descuento con ese codigo."),
            )
        ]

    def __str__(self):
        return self.name

    @property
    def requires_code(self) -> bool:
        return bool(self.code)
