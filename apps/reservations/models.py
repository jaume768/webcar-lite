"""Reserva: version minima, la que necesita el motor de disponibilidad.

Aqui solo estan las fechas, la categoria, las oficinas, el vehiculo opcional y
el estado, que es lo que hace falta para responder "puedo aceptar otra". El
resto de la reserva (cliente, conductores, extras, precio, maquina de estados
completa) llega en su propio prompt.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.contrib.postgres.indexes import GistIndex
from django.db import models
from django.db.models import CheckConstraint, F, Q, Value
from django.utils.translation import gettext_lazy as _

from apps.core.db import TsTzRange
from apps.core.models import TimeStampedModel, UserStampedModel


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


def default_rotation_minutes() -> int:
    return settings.VEHICLE_ROTATION_MINUTES


#: Sin O/0 ni I/1: el localizador se dicta por telefono.
ALFABETO_LOCALIZADOR = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code(longitud: int = 8) -> str:
    return "".join(secrets.choice(ALFABETO_LOCALIZADOR) for _ in range(longitud))


class ReservationQuerySet(models.QuerySet):
    def consuming_capacity(self):
        """Las que ocupan flota. Es el filtro base de toda la disponibilidad."""
        return self.filter(status__in=CAPACITY_CONSUMING_STATUSES)

    def overlapping(self, period):
        return self.filter(occupancy_period__overlap=period)

    def for_category(self, category):
        return self.filter(category=category)


class Reservation(TimeStampedModel, UserStampedModel):
    code = models.CharField(
        _("localizador"),
        max_length=12,
        unique=True,
        help_text=_("Referencia con la que el cliente y el mostrador hablan de la reserva."),
    )

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

    pickup_at = models.DateTimeField(_("recogida"))
    return_at = models.DateTimeField(_("devolucion"))

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

    #: `[pickup_at, occupancy_end)`, calculado por Postgres.
    #:
    #: Es la ocupacion real del coche, no el alquiler: incluye el rato de
    #: limpieza. La suma no se puede hacer aqui porque `timestamptz + interval`
    #: no es inmutable y Postgres no la admite en una columna generada; por eso
    #: el fin va materializado en su propia columna y esta solo lo empaqueta.
    #: Aun asi merece la pena: el rango que ven el indice y la constraint sale
    #: de la base de datos, no de que alguien se acuerde de calcularlo.
    occupancy_period = models.GeneratedField(
        expression=TsTzRange(F("pickup_at"), F("occupancy_end"), Value("[)")),
        output_field=DateTimeRangeField(),
        db_persist=True,
        verbose_name=_("periodo de ocupacion"),
    )

    status = models.CharField(
        _("estado"),
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.DRAFT,
        db_index=True,
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
        ]
        constraints = [
            CheckConstraint(
                condition=Q(return_at__gt=F("pickup_at")),
                name="reservations_devolucion_posterior_a_recogida",
            ),
            # La ocupacion nunca puede acabar antes que el alquiler. Si alguien
            # escribe las fechas con un UPDATE a pelo, salta aqui.
            CheckConstraint(
                condition=Q(occupancy_end__gte=F("return_at")),
                name="reservations_ocupacion_cubre_el_alquiler",
            ),
            # Red de seguridad, no la validacion principal: el motor de
            # disponibilidad ya lo comprueba y da un mensaje legible. Esto es lo
            # unico que llega a tiempo contra dos peticiones simultaneas que
            # asignan el mismo coche.
            #
            # El WHERE parcial deja fuera las reservas sin vehiculo (no hay coche
            # que reservar dos veces) y las que no ocupan flota: una cancelada no
            # puede impedir volver a vender ese hueco.
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
            # La consulta del motor: categoria + solape de periodo.
            GistIndex(
                fields=["category", "occupancy_period"],
                name="reservations_cat_periodo",
            ),
            models.Index(fields=["status", "pickup_at"], name="reservations_estado_recogida"),
            models.Index(fields=["pickup_office", "pickup_at"], name="reservations_oficina_rec"),
        ]

    def __str__(self):
        if self.category_id:
            return f"{self.code} · {self.category.name}"
        return self.code

    def save(self, *args, **kwargs):
        self.occupancy_end = self.return_at + timedelta(minutes=self.rotation_minutes)
        if kwargs.get("update_fields") is not None:
            campos = set(kwargs["update_fields"])
            if campos & {"return_at", "rotation_minutes"}:
                campos.add("occupancy_end")
                kwargs["update_fields"] = campos

        if not self.code:
            # El espacio es de 32^8; el reintento cubre el choque improbable.
            for _intento in range(5):
                candidato = generate_code()
                if not Reservation.objects.filter(code=candidato).exists():
                    self.code = candidato
                    break
            else:  # pragma: no cover - haria falta un choque cinco veces seguidas
                raise RuntimeError("No se ha podido generar un localizador libre.")
        super().save(*args, **kwargs)

    @property
    def consumes_capacity(self) -> bool:
        return self.status in CAPACITY_CONSUMING_STATUSES

    @property
    def is_one_way(self) -> bool:
        return self.pickup_office_id != self.return_office_id
