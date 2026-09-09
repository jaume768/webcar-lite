"""Reglas de la flota. Las vistas orquestan, aqui se decide."""

import structlog
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import (
    MANUAL_STATUSES,
    OPERATIONAL_STATUSES,
    Vehicle,
    VehicleBlock,
    VehicleCategory,
    VehicleStatus,
)

logger = structlog.get_logger(__name__)


class FleetServiceError(ServiceError):
    """Regla de negocio incumplida. La vista la convierte en aviso, no en 500."""


# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------


@transaction.atomic
def save_category(*, category: VehicleCategory, actor=None) -> VehicleCategory:
    """Crea o actualiza una categoria ya validada por su formulario."""
    creando = category.pk is None
    category.code = (category.code or "").strip().lower()
    category.save()
    logger.info(
        "categoria_creada" if creando else "categoria_actualizada",
        category_id=category.pk,
        code=category.code,
        actor_id=getattr(actor, "pk", None),
    )
    return category


@transaction.atomic
def set_category_active(*, category: VehicleCategory, active: bool, actor=None) -> VehicleCategory:
    """Retira una categoria del catalogo o la vuelve a poner. Nunca borra.

    Retirarla no toca nada de lo ya vendido: las reservas historicas siguen
    apuntando a ella y se leen igual. Lo unico que cambia es que deja de
    ofrecerse en reservas nuevas (ver `selectors.selectable_categories`).
    """
    if not active and category.vehicles.filter(is_active=True).exists():
        raise FleetServiceError(
            _("No se puede retirar %(categoria)s: todavia tiene vehiculos activos dentro.")
            % {"categoria": category.name}
        )

    if active:
        category.activate()
    else:
        category.deactivate()
    logger.info(
        "categoria_activada" if active else "categoria_desactivada",
        category_id=category.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return category


# ---------------------------------------------------------------------------
# Vehiculos
# ---------------------------------------------------------------------------


@transaction.atomic
def save_vehicle(*, vehicle: Vehicle, actor=None) -> Vehicle:
    """Crea o actualiza un vehiculo ya validado por su formulario.

    El estado no viaja en el formulario: se cambia con `set_vehicle_status`,
    que es quien sabe que cambios tienen sentido.
    """
    creando = vehicle.pk is None
    vehicle.save()
    logger.info(
        "vehiculo_creado" if creando else "vehiculo_actualizado",
        vehicle_id=vehicle.pk,
        plate=vehicle.plate,
        actor_id=getattr(actor, "pk", None),
    )
    return vehicle


@transaction.atomic
def set_vehicle_status(*, vehicle: Vehicle, status: str, actor=None) -> Vehicle:
    """Cambia a mano el estado de un vehiculo, si el cambio no miente.

    El estado es en buena parte derivado: ALQUILADO lo pone el check-in y lo
    quita el check-out (`start_rental` / `finish_rental`), y RESERVADO lo pone
    la reserva. Desde la ficha solo se tocan los estados de `MANUAL_STATUSES`,
    y nunca sobre un coche que esta fuera en este momento: marcar como
    "disponible" un coche que lleva un cliente dentro es como no tener estado.
    """
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)

    if status not in MANUAL_STATUSES:
        raise FleetServiceError(
            _("El estado '%(estado)s' lo pone la operativa (reserva, entrega o devolucion).")
            % {"estado": VehicleStatus(status).label}
        )

    if vehicle.status in OPERATIONAL_STATUSES:
        raise FleetServiceError(
            _(
                "%(matricula)s esta en '%(estado)s': el cambio tiene que venir de la "
                "reserva o de la devolucion, no de la ficha."
            )
            % {"matricula": vehicle.plate, "estado": vehicle.get_status_display()}
        )

    if status == VehicleStatus.AVAILABLE and vehicle.is_blocked_at():
        raise FleetServiceError(
            _("%(matricula)s tiene un bloqueo activo. Quita el bloqueo y vuelve a intentarlo.")
            % {"matricula": vehicle.plate}
        )

    anterior = vehicle.status
    vehicle.status = status
    vehicle.save(update_fields=["status"])
    logger.info(
        "vehiculo_cambio_de_estado",
        vehicle_id=vehicle.pk,
        plate=vehicle.plate,
        anterior=anterior,
        nuevo=status,
        actor_id=getattr(actor, "pk", None),
    )
    return vehicle


@transaction.atomic
def set_vehicle_active(*, vehicle: Vehicle, active: bool, actor=None) -> Vehicle:
    """Da de baja o vuelve a poner en flota un vehiculo. Nunca borra.

    La baja de flota y el estado BAJA son la misma cosa vista desde dos sitios,
    asi que se mueven juntos: un coche vendido no puede quedarse "disponible".
    """
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)

    if not active and vehicle.status in OPERATIONAL_STATUSES:
        raise FleetServiceError(
            _("%(matricula)s esta en '%(estado)s': no se puede dar de baja hasta que vuelva.")
            % {"matricula": vehicle.plate, "estado": vehicle.get_status_display()}
        )

    vehicle.is_active = active
    vehicle.status = VehicleStatus.AVAILABLE if active else VehicleStatus.RETIRED
    vehicle.save(update_fields=["is_active", "status"])
    logger.info(
        "vehiculo_reactivado" if active else "vehiculo_dado_de_baja",
        vehicle_id=vehicle.pk,
        plate=vehicle.plate,
        actor_id=getattr(actor, "pk", None),
    )
    return vehicle


@transaction.atomic
def start_rental(*, vehicle: Vehicle, actor=None) -> Vehicle:
    """Entrega: el coche pasa a ALQUILADO. Lo llamara el check-in."""
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)

    if vehicle.status == VehicleStatus.RENTED:
        raise FleetServiceError(
            _("%(matricula)s ya figura entregado.") % {"matricula": vehicle.plate}
        )
    if not vehicle.is_active:
        raise FleetServiceError(
            _("%(matricula)s esta dado de baja de la flota.") % {"matricula": vehicle.plate}
        )

    vehicle.status = VehicleStatus.RENTED
    vehicle.save(update_fields=["status"])
    logger.info("vehiculo_entregado", vehicle_id=vehicle.pk, actor_id=getattr(actor, "pk", None))
    return vehicle


@transaction.atomic
def finish_rental(*, vehicle: Vehicle, mileage: int | None = None, actor=None) -> Vehicle:
    """Devolucion: el coche vuelve a LIMPIEZA. Lo llamara el check-out.

    Vuelve a limpieza y no a disponible a proposito: entre que entra y esta
    listo para volver a salir hay trabajo, y la disponibilidad tiene que
    contarlo.
    """
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)

    if vehicle.status != VehicleStatus.RENTED:
        raise FleetServiceError(
            _("%(matricula)s no consta entregado, asi que no se puede devolver.")
            % {"matricula": vehicle.plate}
        )
    if mileage is not None and mileage < vehicle.mileage:
        raise FleetServiceError(
            _("Los kilometros no pueden bajar: el vehiculo tenia %(km)s.") % {"km": vehicle.mileage}
        )

    campos = ["status"]
    vehicle.status = VehicleStatus.CLEANING
    if mileage is not None:
        vehicle.mileage = mileage
        campos.append("mileage")
    vehicle.save(update_fields=campos)
    logger.info("vehiculo_devuelto", vehicle_id=vehicle.pk, actor_id=getattr(actor, "pk", None))
    return vehicle


# ---------------------------------------------------------------------------
# Bloqueos
# ---------------------------------------------------------------------------


@transaction.atomic
def save_block(*, block: VehicleBlock, actor=None) -> VehicleBlock:
    """Guarda un bloqueo y traduce el choque de la base de datos a un aviso.

    El formulario ya avisa de los solapes que ve, pero entre esa comprobacion y
    el INSERT cabe otro usuario guardando lo mismo. Quien llega tarde se lleva
    el IntegrityError de la constraint de exclusion, y aqui se convierte en el
    mismo mensaje legible en lugar de en un 500.
    """
    creando = block.pk is None
    try:
        with transaction.atomic():
            block.save()
    except IntegrityError as exc:
        if "fleet_block_sin_solapes" in str(exc):
            raise FleetServiceError(
                _("Ese vehiculo ya tiene un bloqueo que se solapa con esas fechas.")
            ) from exc
        raise

    logger.info(
        "bloqueo_creado" if creando else "bloqueo_actualizado",
        block_id=block.pk,
        vehicle_id=block.vehicle_id,
        actor_id=getattr(actor, "pk", None),
    )
    return block


@transaction.atomic
def delete_block(*, block: VehicleBlock, actor=None) -> None:
    """Anula un bloqueo.

    Es el unico borrado fisico del sistema y tiene motivo: un bloqueo es un
    apunte de agenda, no un dato historico. Si se anulara marcandolo, seguiria
    ocupando hueco en la constraint de exclusion y el coche no volveria a estar
    libre de verdad.
    """
    datos = {"block_id": block.pk, "vehicle_id": block.vehicle_id}
    block.delete()
    logger.info("bloqueo_anulado", **datos, actor_id=getattr(actor, "pk", None))
