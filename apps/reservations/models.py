"""La reserva: la entidad central del sistema.

Una reserva se vende contra una **categoria**; el vehiculo concreto puede
llegar despues. Y guarda una copia congelada de todo lo que se pacto con el
cliente (precio, extras, politica de combustible, franquicia): si manana sube
una tarifa o cambia el precio de un extra, lo ya vendido no se mueve.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.contrib.postgres.indexes import GistIndex
from django.db import models, transaction
from django.db.models import CheckConstraint, F, Q, Value
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.db import TsTzRange
from apps.core.models import TimeStampedModel, UserStampedModel
from apps.pricing.models import Channel


class ReservationStatus(models.TextChoices):
    DRAFT = "draft", _("Borrador")
    PENDING = "pending", _("Pendiente")
    CONFIRMED = "confirmed", _("Confirmada")
    IN_PROGRESS = "in_progress", _("En curso")
    FINISHED = "finished", _("Finalizada")
    CANCELLED = "cancelled", _("Cancelada")
    NO_SHOW = "no_show", _("No show")


#: Estados que ocupan un coche de verdad y por tanto restan capacidad.
#:
#: BORRADOR no reserva nada todavia; CANCELADA y NO_SHOW liberan el hueco; y
#: FINALIZADA ya devolvio el coche. Esta constante la usan el motor de
#: disponibilidad y la constraint de exclusion, y tienen que decir lo mismo:
#: si cambia, hay que regenerar la constraint.
CAPACITY_CONSUMING_STATUSES = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.IN_PROGRESS,
)

#: Estados desde los que ya no se mueve nada.
FINAL_STATUSES = (
    ReservationStatus.FINISHED,
    ReservationStatus.CANCELLED,
    ReservationStatus.NO_SHOW,
)


class FuelPolicy(models.TextChoices):
    """Como se entrega y como se devuelve el deposito."""

    FULL_FULL = "full_full", _("Lleno - lleno")
    FULL_EMPTY = "full_empty", _("Lleno - vacio")
    SAME_LEVEL = "same_level", _("Mismo nivel")


class CancellationPolicy(models.TextChoices):
    """Que se cobra si el cliente cancela, segun cuanto se anticipe."""

    FLEXIBLE = "flexible", _("Flexible: sin cargo hasta 24 h antes")
    MODERATE = "moderate", _("Moderada: sin cargo hasta 72 h antes")
    STRICT = "strict", _("Estricta: se cobra el primer dia")
    NON_REFUNDABLE = "non_refundable", _("No reembolsable")


def default_rotation_minutes() -> int:
    return settings.VEHICLE_ROTATION_MINUTES


def default_deposit() -> Decimal:
    return Decimal(settings.RESERVATION_DEFAULT_DEPOSIT)


def default_franchise() -> Decimal:
    return Decimal(settings.RESERVATION_DEFAULT_FRANCHISE)


class ReservationCounter(models.Model):
    """Contador de la serie de numeros de reserva.

    Una secuencia de Postgres seria mas comoda, pero salta numeros cuando una
    transaccion se deshace, y aqui la serie no puede tener huecos. Se lleva a
    mano con `SELECT ... FOR UPDATE` sobre esta fila, dentro de la misma
    transaccion que crea la reserva: si la reserva no llega a existir, el
    numero tampoco se consume.
    """

    #: "2026" cuando el formato lleva {year}; "global" cuando la serie es continua.
    scope = models.CharField(_("ambito"), max_length=20, unique=True)
    last_number = models.PositiveIntegerField(_("ultimo numero"), default=0)

    class Meta:
        verbose_name = _("contador de reservas")
        verbose_name_plural = _("contadores de reservas")

    def __str__(self):
        return f"{self.scope}: {self.last_number}"


def next_reservation_number(*, at=None) -> str:
    """Siguiente numero de la serie, sin huecos y sin repetir.

    Se llama dentro de la transaccion que crea la reserva. El bloqueo sobre la
    fila del contador pone en fila a las altas simultaneas.
    """
    formato = settings.RESERVATION_NUMBER_FORMAT
    momento = at or timezone.now()
    ambito = str(timezone.localtime(momento).year) if "{year}" in formato else "global"

    # atomic anidado: si ya hay transaccion, es un savepoint y el contador se
    # deshace con ella; si no la hay, abre la suya para poder bloquear.
    with transaction.atomic():
        contador, _creado = ReservationCounter.objects.get_or_create(scope=ambito)
        contador = ReservationCounter.objects.select_for_update().get(pk=contador.pk)
        contador.last_number += 1
        contador.save(update_fields=["last_number"])
        return formato.format(year=ambito, sequence=contador.last_number)


class ReservationQuerySet(models.QuerySet):
    def consuming_capacity(self):
        """Las que ocupan flota. Es el filtro base de toda la disponibilidad."""
        return self.filter(status__in=CAPACITY_CONSUMING_STATUSES)

    def overlapping(self, period):
        return self.filter(occupancy_period__overlap=period)

    def for_category(self, category):
        return self.filter(category=category)

    def for_user(self, user):
        """Scope de oficina: solo lo que sale o entra en sus oficinas."""
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        oficinas = user.offices.all()
        return self.filter(Q(pickup_office__in=oficinas) | Q(return_office__in=oficinas)).distinct()


class Reservation(TimeStampedModel, UserStampedModel):
    number = models.CharField(
        _("numero"),
        max_length=30,
        unique=True,
        editable=False,
        help_text=_("Serie correlativa sin huecos. Formato en RESERVATION_NUMBER_FORMAT."),
    )

    status = models.CharField(
        _("estado"),
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.DRAFT,
        db_index=True,
    )

    # --- que se alquila ----------------------------------------------------
    category = models.ForeignKey(
        "fleet.VehicleCategory",
        verbose_name=_("categoria"),
        on_delete=models.PROTECT,
        related_name="reservations",
        help_text=_("Lo que se vende. El vehiculo concreto puede llegar despues."),
    )
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        verbose_name=_("vehiculo"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservations",
        help_text=_("Opcional: una reserva puede existir contra la categoria."),
    )

    # --- donde y cuando ----------------------------------------------------
    pickup_office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina de recogida"),
        on_delete=models.PROTECT,
        related_name="pickups",
    )
    return_office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina de devolucion"),
        on_delete=models.PROTECT,
        related_name="returns",
    )

    pickup_at = models.DateTimeField(_("recogida prevista"))
    return_at = models.DateTimeField(_("devolucion prevista"))
    actual_pickup_at = models.DateTimeField(
        _("recogida real"),
        null=True,
        blank=True,
        help_text=_("La escribe el check-in. Sin ella la reserva no pasa a en curso."),
    )
    actual_return_at = models.DateTimeField(
        _("devolucion real"),
        null=True,
        blank=True,
        help_text=_("La escribe el check-out. Sin ella la reserva no se finaliza."),
    )

    # --- quien -------------------------------------------------------------
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("cliente"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservations",
        help_text=_("Un borrador puede no tenerlo todavia; para confirmar es obligatorio."),
    )

    # --- condiciones comerciales -------------------------------------------
    rate = models.ForeignKey(
        "pricing.Rate",
        verbose_name=_("tarifa"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservations",
        help_text=_("La que se aplico. Se conserva aunque despues se retire del catalogo."),
    )
    channel = models.CharField(
        _("canal"),
        max_length=20,
        choices=Channel.choices,
        default=Channel.COUNTER,
        db_index=True,
    )

    fuel_policy = models.CharField(
        _("politica de combustible"),
        max_length=20,
        choices=FuelPolicy.choices,
        default=FuelPolicy.FULL_FULL,
    )
    included_km = models.PositiveIntegerField(
        _("kilometros incluidos"),
        null=True,
        blank=True,
        help_text=_("En blanco: kilometraje ilimitado."),
    )
    cancellation_policy = models.CharField(
        _("politica de cancelacion"),
        max_length=20,
        choices=CancellationPolicy.choices,
        default=CancellationPolicy.FLEXIBLE,
    )
    cancellation_fee = models.DecimalField(
        _("cargo por cancelacion"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Lo calcula la politica al cancelar. Se congela en ese momento."),
    )

    deposit_amount = models.DecimalField(
        _("fianza"), max_digits=10, decimal_places=2, default=default_deposit
    )
    franchise_amount = models.DecimalField(
        _("franquicia"), max_digits=10, decimal_places=2, default=default_franchise
    )

    # --- precio congelado ---------------------------------------------------
    # Copia del calculo, no una referencia a las tarifas: el desglose de una
    # reserva de hace un ano tiene que poder leerse tal como se vendio.
    base_amount = models.DecimalField(
        _("base del alquiler"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    # Los totales por concepto son **base imponible**; el impuesto de todas las
    # lineas va junto en `tax_total`, y `total` es la suma de todo. Es la misma
    # convencion que usa el motor de tarifas, para que el desglose guardado y
    # los campos digan exactamente lo mismo.
    extras_total = models.DecimalField(
        _("total de extras (base)"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    supplements_total = models.DecimalField(
        _("total de suplementos (base)"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    discounts_total = models.DecimalField(
        _("total de descuentos (base)"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    tax_total = models.DecimalField(
        _("total de impuestos"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        _("total"), max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    price_breakdown = models.JSONField(
        _("desglose"),
        default=dict,
        blank=True,
        help_text=_("El calculo entero tal como salio, linea a linea."),
    )

    is_price_manual = models.BooleanField(
        _("precio manual"),
        default=False,
        help_text=_("Se ha tocado el precio a mano. Exige permiso y motivo."),
    )
    manual_price_reason = models.TextField(_("motivo del precio manual"), blank=True, default="")

    # --- notas --------------------------------------------------------------
    notes = models.TextField(
        _("notas"),
        blank=True,
        default="",
        help_text=_("Lo que ve el cliente en el contrato."),
    )
    internal_notes = models.TextField(
        _("notas internas"),
        blank=True,
        default="",
        help_text=_("Solo para el mostrador. No se imprime."),
    )

    # --- disponibilidad -----------------------------------------------------
    rotation_minutes = models.PositiveSmallIntegerField(
        _("minutos de rotacion"),
        default=default_rotation_minutes,
        help_text=_(
            "Limpieza y revision despues de esta entrega. Se guarda en la reserva "
            "y no en la configuracion: cambiar el valor general no puede mover "
            "hacia atras la ocupacion de lo ya reservado."
        ),
    )
    occupancy_end = models.DateTimeField(
        _("fin de ocupacion"),
        editable=False,
        help_text=_("Devolucion mas la rotacion. Lo escribe save(), no se teclea."),
    )
    occupancy_period = models.GeneratedField(
        expression=TsTzRange(F("pickup_at"), F("occupancy_end"), Value("[)")),
        output_field=DateTimeRangeField(),
        db_persist=True,
        verbose_name=_("periodo de ocupacion"),
    )

    needs_reassignment = models.BooleanField(
        _("pendiente de reasignar"),
        default=False,
        db_index=True,
        help_text=_(
            "El vehiculo que tenia asignado ya no esta en flota. La reserva sigue "
            "viva y hay que darle otro coche."
        ),
    )

    overbooked = models.BooleanField(
        _("forzada sin disponibilidad"),
        default=False,
        help_text=_("Se acepto por encima de la capacidad, con permiso y motivo."),
    )
    override_reason = models.TextField(_("motivo del overbooking"), blank=True)
    override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("forzada por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="forced_reservations",
    )

    objects = ReservationQuerySet.as_manager()

    class Meta:
        verbose_name = _("reserva")
        verbose_name_plural = _("reservas")
        ordering = ["-pickup_at"]
        permissions = [
            ("change_reservation_price", _("Puede modificar el precio de una reserva a mano")),
            ("cancel_reservation", _("Puede cancelar una reserva")),
            (
                "change_invoiced_reservation",
                _("Puede modificar una reserva que ya tiene factura emitida"),
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(return_at__gt=F("pickup_at")),
                name="reservations_devolucion_posterior_a_recogida",
            ),
            CheckConstraint(
                condition=Q(occupancy_end__gte=F("return_at")),
                name="reservations_ocupacion_cubre_el_alquiler",
            ),
            ExclusionConstraint(
                name="reservations_vehiculo_sin_solapes",
                expressions=[
                    ("occupancy_period", RangeOperators.OVERLAPS),
                    ("vehicle", RangeOperators.EQUAL),
                ],
                condition=Q(vehicle__isnull=False, status__in=CAPACITY_CONSUMING_STATUSES),
                violation_error_message=_(
                    "Ese vehiculo ya esta comprometido en parte de ese periodo."
                ),
            ),
        ]
        indexes = [
            GistIndex(fields=["category", "occupancy_period"], name="reservations_cat_periodo"),
            models.Index(fields=["status", "pickup_at"], name="reservations_estado_recogida"),
            models.Index(fields=["pickup_office", "pickup_at"], name="reservations_oficina_rec"),
            models.Index(fields=["customer", "-pickup_at"], name="reservations_cliente"),
        ]

    def __str__(self):
        if self.category_id:
            return f"{self.number} · {self.category.name}"
        return self.number

    def save(self, *args, **kwargs):
        self.occupancy_end = self.return_at + timedelta(minutes=self.rotation_minutes)
        if kwargs.get("update_fields") is not None:
            campos = set(kwargs["update_fields"])
            if campos & {"return_at", "rotation_minutes"}:
                campos.add("occupancy_end")
                kwargs["update_fields"] = campos

        if not self.number:
            self.number = next_reservation_number(at=self.pickup_at)
        super().save(*args, **kwargs)

    # --- lecturas de conveniencia ------------------------------------------

    @property
    def consumes_capacity(self) -> bool:
        return self.status in CAPACITY_CONSUMING_STATUSES

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    @property
    def is_one_way(self) -> bool:
        return self.pickup_office_id != self.return_office_id

    @property
    def has_unlimited_km(self) -> bool:
        return self.included_km is None


class ReservationExtra(models.Model):
    """Un extra vendido con la reserva, con el precio congelado.

    No se lee del maestro al mostrar: si manana sube la silla infantil, la
    reserva de ayer sigue diciendo lo que se cobro. Por eso viajan aqui copiados
    el concepto, el precio unitario y el impuesto.
    """

    reservation = models.ForeignKey(
        Reservation,
        verbose_name=_("reserva"),
        on_delete=models.CASCADE,
        related_name="extras",
    )
    extra = models.ForeignKey(
        "pricing.Extra",
        verbose_name=_("extra"),
        on_delete=models.PROTECT,
        related_name="reservation_lines",
    )
    concept = models.CharField(
        _("concepto"),
        max_length=120,
        help_text=_("Nombre del extra cuando se vendio."),
    )

    quantity = models.PositiveSmallIntegerField(_("cantidad"), default=1)
    unit_price = models.DecimalField(
        _("precio unitario"),
        max_digits=10,
        decimal_places=2,
        help_text=_(
            "Precio de una unidad por todo el alquiler, no por dia: es la linea "
            "tal como sale en el contrato y en la factura."
        ),
    )
    tax_rate = models.DecimalField(_("tipo de impuesto"), max_digits=5, decimal_places=2)

    base_amount = models.DecimalField(_("base imponible"), max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(_("impuesto"), max_digits=10, decimal_places=2)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = _("extra de la reserva")
        verbose_name_plural = _("extras de la reserva")
        ordering = ["concept"]
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "extra"],
                name="reservations_un_extra_una_vez_por_reserva",
            ),
        ]

    def __str__(self):
        return f"{self.concept} x{self.quantity}"


class ReservationDriver(models.Model):
    """Conductor adicional autorizado.

    Los datos del carnet van copiados aunque el conductor sea ya cliente: lo que
    vale es lo que se comprobo el dia de la entrega.
    """

    reservation = models.ForeignKey(
        Reservation,
        verbose_name=_("reserva"),
        on_delete=models.CASCADE,
        related_name="drivers",
    )
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("ficha de cliente"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="driving_for",
        help_text=_("Si ademas es cliente. No es obligatorio."),
    )

    first_name = models.CharField(_("nombre"), max_length=80)
    last_name = models.CharField(_("apellidos"), max_length=120)
    birth_date = models.DateField(_("fecha de nacimiento"), null=True, blank=True)
    document_number = models.CharField(_("numero de documento"), max_length=20, blank=True)

    licence_number = models.CharField(_("numero de carnet"), max_length=30)
    licence_country = models.CharField(_("pais del carnet"), max_length=2, default="ES")
    licence_issued_on = models.DateField(_("expedicion del carnet"), null=True, blank=True)
    licence_expiry = models.DateField(_("caducidad del carnet"), null=True, blank=True)

    class Meta:
        verbose_name = _("conductor adicional")
        verbose_name_plural = _("conductores adicionales")
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def licence_expired(self) -> bool:
        return self.licence_expiry is not None and self.licence_expiry < timezone.localdate()


class ReservationStatusChange(models.Model):
    """Historico de transiciones. Solo lo escribe la maquina de estados.

    Es la respuesta a "quien cambio esto y por que". No se edita ni se borra:
    una fila mal puesta aqui es peor que no tenerla.
    """

    reservation = models.ForeignKey(
        Reservation,
        verbose_name=_("reserva"),
        on_delete=models.CASCADE,
        related_name="status_changes",
    )
    from_status = models.CharField(
        _("estado anterior"), max_length=20, choices=ReservationStatus.choices
    )
    to_status = models.CharField(
        _("estado nuevo"), max_length=20, choices=ReservationStatus.choices
    )
    reason = models.TextField(_("motivo"), blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("hecho por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservation_status_changes",
    )
    created_at = models.DateTimeField(_("cuando"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("cambio de estado")
        verbose_name_plural = _("cambios de estado")
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.reservation_id}: {self.from_status} -> {self.to_status}"
