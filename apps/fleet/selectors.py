"""Consultas de flota.

Aqui vive la diferencia entre "lo que se puede vender hoy" y "lo que existe".
Una categoria retirada del catalogo sigue existiendo (y se lee en el historico),
pero no se ofrece en una reserva nueva. Cualquier selector de venta pasa por
`selectable_categories()`: si cada formulario lo resolviera por su cuenta,
antes o despues uno se olvidaria del filtro.
"""

from django.utils import timezone

from .models import Vehicle, VehicleBlock, VehicleCategory, VehicleStatus


def selectable_categories():
    """Categorias que se pueden elegir al crear o modificar una reserva."""
    return VehicleCategory.objects.active().order_by("sort_order", "name")


def all_categories():
    """Todas, activas y retiradas. Para listados e historico, nunca para vender."""
    return VehicleCategory.objects.all().order_by("sort_order", "name")


def vehicles_for_user(user):
    """Flota que el usuario puede ver: la de sus oficinas."""
    return Vehicle.objects.for_user(user).select_related("category", "current_office")


def assignable_vehicles(user, *, category=None, office=None):
    """Coches que se pueden asignar hoy a una entrega.

    Activos, disponibles y sin bloqueo vivo. La disponibilidad por fechas la
    resolvera el motor de `availability`; esto es la foto de este momento, que
    es lo que necesita el mostrador para asignar un coche ahora.
    """
    ahora = timezone.now()
    consulta = Vehicle.objects.for_user(user).active().filter(status=VehicleStatus.AVAILABLE)
    if category is not None:
        consulta = consulta.filter(category=category)
    if office is not None:
        consulta = consulta.filter(current_office=office)
    bloqueados = VehicleBlock.objects.filter(start_at__lte=ahora, end_at__gt=ahora).values(
        "vehicle_id"
    )
    return consulta.exclude(pk__in=bloqueados).select_related("category", "current_office")


def blocks_for_user(user):
    """Bloqueos de la flota que el usuario puede ver."""
    return VehicleBlock.objects.filter(vehicle__in=Vehicle.objects.for_user(user)).select_related(
        "vehicle", "vehicle__current_office"
    )
