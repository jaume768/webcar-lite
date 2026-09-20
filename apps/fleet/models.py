"""Flota: categorias, vehiculos y bloqueos manuales.

La categoria es la unidad con la que se vende y con la que se calcula la
disponibilidad: una reserva puede existir contra una categoria sin vehiculo
asignado, asi que la categoria vive por su cuenta y no depende de que haya
coches dados de alta.
"""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import CheckConstraint, F, Func, Q, Value
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.scoping import OfficeScopedQuerySet
from apps.core.models import ActivableModel, ActivableQuerySet, TimeStampedModel, UserStampedModel


class Transmission(models.TextChoices):
    MANUAL = "manual", _("Manual")
    AUTOMATIC = "automatic", _("Automatico")


class Fuel(models.TextChoices):
    PETROL = "petrol", _("Gasolina")
    DIESEL = "diesel", _("Diesel")
    HYBRID = "hybrid", _("Hibrido")
    PLUGIN_HYBRID = "plugin_hybrid", _("Hibrido enchufable")
    ELECTRIC = "electric", _("Electrico")
    LPG = "lpg", _("GLP")


class VehicleStatus(models.TextChoices):
    """Situacion real del coche ahora mismo.

    En su mayor parte es un estado **derivado** de la operativa: el check-in lo
    pone en ALQUILADO y el check-out lo devuelve. Los estados que puede fijar
    una persona a mano estan en `MANUAL_STATUSES`; el resto los escribe el
    proceso que corresponde, para que la pantalla no pueda contradecir a la
    realidad del mostrador.
    """

    AVAILABLE = "available", _("Disponible")
    RESERVED = "reserved", _("Reservado")
    RENTED = "rented", _("Alquilado")
    WORKSHOP = "workshop", _("Taller")
    CLEANING = "cleaning", _("Limpieza")
    BLOCKED = "blocked", _("Bloqueado")
    RETIRED = "retired", _("Baja")


#: Estados que se pueden poner a mano desde la ficha del vehiculo.
MANUAL_STATUSES = frozenset(
    {
        VehicleStatus.AVAILABLE,
        VehicleStatus.WORKSHOP,
        VehicleStatus.CLEANING,
        VehicleStatus.BLOCKED,
        VehicleStatus.RETIRED,
    }
)

#: Estados que escribe la operativa (reservas, check-in y check-out) y que no se
#: tocan desde la ficha: quien los cambia es el proceso, no una persona.
OPERATIONAL_STATUSES = frozenset({VehicleStatus.RESERVED, VehicleStatus.RENTED})

#: Estados en los que el vehiculo no se puede entregar.
UNAVAILABLE_STATUSES = frozenset(
    {
        VehicleStatus.WORKSHOP,
        VehicleStatus.CLEANING,
        VehicleStatus.BLOCKED,
        VehicleStatus.RETIRED,
    }
)


class BlockReason(models.TextChoices):
    WORKSHOP = "workshop", _("Taller")
    CLEANING = "cleaning", _("Limpieza")
    LONG_TERM = "long_term", _("Larga duracion")
    TRANSFER = "transfer", _("Traslado")
    OTHER = "other", _("Otros")


validar_matricula = RegexValidator(
    # Ni el formato espanol ni el extranjero se pueden cerrar del todo: la flota
    # puede tener coches matriculados fuera. Se exige algo con pinta de
    # matricula y se deja la forma exacta a quien la teclea.
    regex=r"^[A-Z0-9]{4,10}$",
    message=_("La matricula son entre 4 y 10 letras o numeros, sin espacios ni guiones."),
)


class VehicleCategory(TimeStampedModel, ActivableModel):
    """Grupo comercial de vehiculos (ACRISS de andar por casa: economico, SUV...).

    No se borra nunca: las reservas y las facturas de hace dos anos apuntan a
    la categoria con la que se vendieron. Al retirarla del catalogo se
    desactiva, y entonces deja de ofrecerse en reservas nuevas pero se sigue
    leyendo en el historico.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe una categoria con ese codigo.")},
        help_text=_("Identificador corto y estable. Aparece en tarifas y contratos."),
    )
    name = models.CharField(_("nombre"), max_length=120)
    description = models.TextField(_("descripcion"), blank=True)
    image = models.ImageField(
        _("imagen"),
        upload_to="categorias/",
        blank=True,
        help_text=_("Foto de referencia del grupo. No es un vehiculo concreto."),
    )

    seats = models.PositiveSmallIntegerField(_("plazas"), default=5)
    doors = models.PositiveSmallIntegerField(_("puertas"), default=5)
    luggage = models.PositiveSmallIntegerField(
        _("maletas"),
        default=2,
        help_text=_("Maletas grandes que caben en el maletero."),
    )
    transmission = models.CharField(
        _("cambio"),
        max_length=20,
        choices=Transmission.choices,
        default=Transmission.MANUAL,
    )
    fuel = models.CharField(
        _("combustible"),
        max_length=20,
        choices=Fuel.choices,
        default=Fuel.PETROL,
    )
    air_conditioning = models.BooleanField(_("aire acondicionado"), default=True)

    sort_order = models.PositiveSmallIntegerField(
        _("orden"),
        default=100,
        db_index=True,
        help_text=_("Menor primero. Fija como se ordenan en los selectores."),
    )

    class Meta:
        verbose_name = _("categoria de vehiculo")
        verbose_name_plural = _("categorias de vehiculo")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class VehicleQuerySet(ActivableQuerySet, OfficeScopedQuerySet):
    """Baja logica y scope de oficina en el mismo queryset.

    Un vehiculo es dato operativo: quien solo tiene una oficina no ve la flota de
    otra ni escribiendo la URL a mano.
    """

    def available(self):
        return self.active().filter(status=VehicleStatus.AVAILABLE)


class VehicleManager(models.Manager.from_queryset(VehicleQuerySet)):
    """Manager por defecto. No filtra solo: hay que pedir `active()`/`for_user()`."""


class Vehicle(TimeStampedModel, ActivableModel, UserStampedModel):
    """Un coche concreto de la flota.

    La oficina es la de estacionamiento actual (`current_office`), no la de
    compra: es la que manda para el scope de datos y para la disponibilidad.
    """

    OFFICE_FIELD = "current_office"

    plate = models.CharField(
        _("matricula"),
        max_length=10,
        unique=True,
        validators=[validar_matricula],
        error_messages={"unique": _("Ya hay un vehiculo con esa matricula.")},
    )
    brand = models.CharField(_("marca"), max_length=60)
    model = models.CharField(_("modelo"), max_length=80)
    version = models.CharField(_("version"), max_length=80, blank=True)

    category = models.ForeignKey(
        VehicleCategory,
        verbose_name=_("categoria"),
        on_delete=models.PROTECT,
        related_name="vehicles",
    )
    current_office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina actual"),
        on_delete=models.PROTECT,
        related_name="vehicles",
        help_text=_("Donde esta aparcado ahora. Cambia con los traslados."),
    )

    vin = models.CharField(
        _("bastidor"),
        max_length=17,
        blank=True,
        help_text=_("Numero de bastidor (VIN). 17 caracteres."),
    )
    mileage = models.PositiveIntegerField(
        _("kilometros"),
        default=0,
        help_text=_("Ultima lectura conocida. La actualiza el check-out."),
    )

    fuel = models.CharField(_("combustible"), max_length=20, choices=Fuel.choices)
    transmission = models.CharField(_("cambio"), max_length=20, choices=Transmission.choices)
    seats = models.PositiveSmallIntegerField(_("plazas"), default=5)
    color = models.CharField(_("color"), max_length=40, blank=True)
    tank_liters = models.PositiveSmallIntegerField(
        _("deposito (litros)"),
        null=True,
        blank=True,
        help_text=_(
            "Capacidad del deposito. Con ella se calcula el combustible que "
            "falta al devolver. En blanco se usa el valor por defecto."
        ),
    )

    registration_date = models.DateField(_("primera matriculacion"), null=True, blank=True)
    itv_expiry = models.DateField(
        _("caducidad de la ITV"),
        null=True,
        blank=True,
        help_text=_("Con la ITV caducada el coche no sale del parking."),
    )
    insurance_expiry = models.DateField(_("caducidad del seguro"), null=True, blank=True)
    insurance_company = models.CharField(_("aseguradora"), max_length=80, blank=True)
    insurance_policy = models.CharField(_("numero de poliza"), max_length=60, blank=True)

    status = models.CharField(
        _("estado"),
        max_length=20,
        choices=VehicleStatus.choices,
        default=VehicleStatus.AVAILABLE,
        db_index=True,
    )

    purchase_date = models.DateField(_("fecha de compra"), null=True, blank=True)
    notes = models.TextField(_("notas"), blank=True)

    objects = VehicleManager()

    class Meta:
        verbose_name = _("vehiculo")
        verbose_name_plural = _("vehiculos")
        ordering = ["plate"]
        indexes = [
            models.Index(fields=["current_office", "status"], name="fleet_vehicle_office_status"),
            models.Index(fields=["category", "status"], name="fleet_vehicle_cat_status"),
        ]

    def __str__(self):
        return f"{self.plate} · {self.brand} {self.model}".strip()

    def save(self, *args, **kwargs):
        self.plate = (self.plate or "").replace(" ", "").replace("-", "").upper()
        super().save(*args, **kwargs)

    @property
    def documentation_expired(self) -> bool:
        """ITV o seguro caducados. El mostrador tiene que verlo de un vistazo."""
        hoy = timezone.localdate()
        return any(
            fecha is not None and fecha < hoy for fecha in (self.itv_expiry, self.insurance_expiry)
        )

    def is_blocked_at(self, momento=None) -> bool:
        """True si hay un bloqueo manual vivo en ese instante."""
        momento = momento or timezone.now()
        return self.blocks.filter(start_at__lte=momento, end_at__gt=momento).exists()


class TsTzRange(Func):
    """`tstzrange(inicio, fin, '[)')` para usarlo dentro de una constraint."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class VehicleBlock(TimeStampedModel, UserStampedModel):
    """Bloqueo manual de un vehiculo entre dos instantes.

    Taller, limpieza larga, un traslado entre oficinas o una cesion de larga
    duracion: rato en el que el coche existe pero no se puede alquilar. La
    disponibilidad lo resta igual que una reserva.

    A diferencia de los maestros, un bloqueo **si** se borra: es un apunte de
    agenda, no un dato historico, y si se anula tiene que dejar de ocupar hueco
    de verdad (la constraint de exclusion cuenta todas las filas que haya).
    """

    vehicle = models.ForeignKey(
        Vehicle,
        verbose_name=_("vehiculo"),
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    start_at = models.DateTimeField(_("desde"))
    end_at = models.DateTimeField(_("hasta"))
    reason = models.CharField(
        _("motivo"),
        max_length=20,
        choices=BlockReason.choices,
        default=BlockReason.WORKSHOP,
    )
    notes = models.TextField(_("notas"), blank=True)

    # `created_by` y `updated_by` los pone UserStampedModel con el usuario de la
    # request: quien bloqueo un coche es justo lo que se pregunta despues.

    class Meta:
        verbose_name = _("bloqueo de vehiculo")
        verbose_name_plural = _("bloqueos de vehiculo")
        ordering = ["-start_at"]
        constraints = [
            CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="fleet_block_fin_posterior_al_inicio",
            ),
            # Red de seguridad en la base de datos: dos bloqueos del mismo coche
            # no pueden pisarse. El formulario tambien lo comprueba y da un
            # mensaje legible, pero contra una condicion de carrera el unico
            # que llega a tiempo es Postgres.
            ExclusionConstraint(
                name="fleet_block_sin_solapes",
                expressions=[
                    (TsTzRange("start_at", "end_at", Value("[)")), RangeOperators.OVERLAPS),
                    ("vehicle", RangeOperators.EQUAL),
                ],
                violation_error_message=_(
                    "Ese vehiculo ya tiene un bloqueo que se solapa con esas fechas."
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["vehicle", "start_at"], name="fleet_block_vehicle_start"),
        ]

    def __str__(self):
        return f"{self.vehicle.plate}: {self.get_reason_display()}"

    @property
    def is_current(self) -> bool:
        return self.start_at <= timezone.now() < self.end_at

    @property
    def is_past(self) -> bool:
        return self.end_at <= timezone.now()
