"""Informes de explotacion: que gana cada coche y cuanto se usa la flota.

Dos reglas guian todo el modulo:

- **El calculo es puro.** Las funciones `*_rows` reciben listas de datos simples
  (tuplas y decimales) y devuelven resultados; se prueban sin base de datos y
  sin cliente HTTP. Las funciones `*_report` son las que consultan y delegan.
- **Se cuenta por ocupacion real, no por facturacion.** Un coche alquilado
  ocupa sitio aunque la factura se emita el mes que viene, asi que el reparto
  por meses se hace con los dias que el coche estuvo fuera.

Lo que no esta aqui: el IVA, los cobros y las facturas. Eso es `billing`, y para
la gestoria se exporta tal cual en `exports.py`.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from apps.fleet.models import Vehicle
from apps.reservations.models import Reservation, ReservationStatus

CERO = Decimal("0.00")

#: Reservas que cuentan como negocio: las canceladas y los no-show no han
#: ocupado el coche ni han entrado en caja.
ESTADOS_PRODUCTIVOS = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.IN_PROGRESS,
    ReservationStatus.FINISHED,
)


def _euros(valor: Decimal) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_start(dia: date) -> date:
    return dia.replace(day=1)


def next_month(dia: date) -> date:
    return (dia.replace(day=28) + timedelta(days=4)).replace(day=1)


def months_between(desde: date, hasta: date) -> list[date]:
    """Primeros de mes entre dos fechas, ambas incluidas."""
    meses, actual = [], month_start(desde)
    tope = month_start(hasta)
    while actual <= tope:
        meses.append(actual)
        actual = next_month(actual)
    return meses


def _rango(desde: date, hasta: date) -> tuple[datetime, datetime]:
    """El periodo en instantes con zona, de las 00:00 del primer dia a las
    00:00 del siguiente al ultimo."""
    zona = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(desde, datetime.min.time()), zona)
    fin = timezone.make_aware(
        datetime.combine(hasta + timedelta(days=1), datetime.min.time()), zona
    )
    return inicio, fin


# ---------------------------------------------------------------------------
# Ingresos por coche
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VehicleRevenue:
    """Lo que ha dado un coche en el periodo."""

    vehicle_id: int
    plate: str
    label: str
    category: str
    reservations: int
    days: int
    revenue: Decimal

    @property
    def revenue_per_day(self) -> Decimal:
        if not self.days:
            return CERO
        return _euros(self.revenue / self.days)


def revenue_rows(filas: list[dict]) -> list[VehicleRevenue]:
    """Agrupa por coche las reservas ya leidas de la base de datos.

    Cada fila trae: vehicle_id, plate, label, category, days y revenue. El
    calculo es una suma, pero se hace aqui para poder probarlo solo.
    """
    acumulado: dict[int, dict] = {}
    for fila in filas:
        dato = acumulado.setdefault(
            fila["vehicle_id"],
            {
                "plate": fila["plate"],
                "label": fila["label"],
                "category": fila["category"],
                "reservations": 0,
                "days": 0,
                "revenue": CERO,
            },
        )
        dato["reservations"] += 1
        dato["days"] += fila["days"]
        dato["revenue"] += Decimal(fila["revenue"] or 0)

    resultado = [
        VehicleRevenue(
            vehicle_id=vehicle_id,
            plate=dato["plate"],
            label=dato["label"],
            category=dato["category"],
            reservations=dato["reservations"],
            days=dato["days"],
            revenue=_euros(dato["revenue"]),
        )
        for vehicle_id, dato in acumulado.items()
    ]
    # De mas a menos ingresos: la primera pregunta siempre es cual da mas.
    return sorted(resultado, key=lambda fila: (-fila.revenue, fila.plate))


def revenue_by_vehicle(*, user, desde: date, hasta: date) -> list[VehicleRevenue]:
    """Ingresos por coche de las reservas recogidas dentro del periodo.

    Se imputa por fecha de recogida: es la que entiende quien lleva la flota
    ("este coche salio doce veces en julio") y no parte un alquiler en dos.
    """
    inicio, fin = _rango(desde, hasta)
    reservas = (
        Reservation.objects.for_user(user)
        .filter(
            vehicle__isnull=False,
            status__in=ESTADOS_PRODUCTIVOS,
            pickup_at__gte=inicio,
            pickup_at__lt=fin,
        )
        .select_related("vehicle", "vehicle__category", "category")
    )

    filas = []
    for reserva in reservas:
        vehiculo = reserva.vehicle
        filas.append(
            {
                "vehicle_id": vehiculo.pk,
                "plate": vehiculo.plate,
                "label": f"{vehiculo.brand} {vehiculo.model}".strip(),
                "category": vehiculo.category.name if vehiculo.category_id else "",
                "days": rented_days(reserva.pickup_at, reserva.return_at),
                # `grand_total` es el alquiler mas los cargos de la devolucion:
                # lo que el cliente acaba debiendo por ese coche.
                "revenue": reserva.grand_total,
            }
        )
    return revenue_rows(filas)


def rented_days(pickup_at: datetime, return_at: datetime) -> int:
    """Dias de alquiler a efectos de informe.

    No se usa `pricing.rental_days` a proposito: alli se decide lo que se
    factura (con su margen de cortesia) y aqui se mide cuanto tiempo estuvo el
    coche fuera. Un dia empezado cuenta como dia.
    """
    horas = (return_at - pickup_at).total_seconds() / 3600
    return max(1, int(-(-horas // 24)))


# ---------------------------------------------------------------------------
# Ocupacion por mes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthOccupancy:
    month: date
    vehicles: int
    available_days: int
    rented_days: int

    @property
    def label(self) -> str:
        return date_format(self.month, "F Y").capitalize()

    @property
    def rate(self) -> Decimal:
        """Porcentaje de ocupacion con un decimal."""
        if not self.available_days:
            return CERO
        porcentaje = Decimal(self.rented_days) * 100 / Decimal(self.available_days)
        return porcentaje.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def days_in_month(mes: date) -> int:
    return (next_month(mes) - mes).days


def occupancy_rows(
    *, meses: list[date], vehiculos: int, periodos: list[tuple[date, date]]
) -> list[MonthOccupancy]:
    """Reparte por meses los dias ocupados.

    `periodos` son pares (primer dia fuera, ultimo dia fuera), ambos incluidos.
    Un alquiler de fin de mes suma sus dias a cada mes que toca, que es la unica
    forma de que la ocupacion de un mes signifique algo.
    """
    ocupados = dict.fromkeys(meses, 0)
    for inicio, fin in periodos:
        for mes in meses:
            ultimo = next_month(mes) - timedelta(days=1)
            solape = (min(fin, ultimo) - max(inicio, mes)).days + 1
            if solape > 0:
                ocupados[mes] += solape

    return [
        MonthOccupancy(
            month=mes,
            vehicles=vehiculos,
            available_days=vehiculos * days_in_month(mes),
            rented_days=ocupados[mes],
        )
        for mes in meses
    ]


def occupancy_by_month(*, user, desde: date, hasta: date) -> list[MonthOccupancy]:
    """Ocupacion mes a mes de la flota activa.

    La flota de referencia son los vehiculos dados de alta hoy: un coche
    vendido el ano pasado ya no forma parte del negocio que se esta midiendo.
    """
    meses = months_between(desde, hasta)
    if not meses:
        return []

    inicio, fin = _rango(meses[0], next_month(meses[-1]) - timedelta(days=1))
    vehiculos = Vehicle.objects.active().for_user(user).count()

    zona = timezone.get_current_timezone()
    periodos = [
        (
            timezone.localtime(reserva.pickup_at, zona).date(),
            timezone.localtime(reserva.return_at, zona).date(),
        )
        for reserva in Reservation.objects.for_user(user)
        .filter(vehicle__isnull=False, status__in=ESTADOS_PRODUCTIVOS)
        .filter(Q(pickup_at__lt=fin) & Q(return_at__gte=inicio))
        .only("pickup_at", "return_at")
    ]
    return occupancy_rows(meses=meses, vehiculos=vehiculos, periodos=periodos)


# ---------------------------------------------------------------------------
# Totales de cabecera
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueSummary:
    vehicles: int
    reservations: int
    days: int
    revenue: Decimal

    @property
    def revenue_per_vehicle(self) -> Decimal:
        if not self.vehicles:
            return CERO
        return _euros(self.revenue / self.vehicles)

    @property
    def revenue_per_day(self) -> Decimal:
        if not self.days:
            return CERO
        return _euros(self.revenue / self.days)


def summarize(filas: list[VehicleRevenue]) -> RevenueSummary:
    return RevenueSummary(
        vehicles=len(filas),
        reservations=sum(fila.reservations for fila in filas),
        days=sum(fila.days for fila in filas),
        revenue=_euros(sum((fila.revenue for fila in filas), CERO)),
    )


#: Etiquetas de los periodos que ofrece la pantalla.
PERIODOS = {
    "mes": _("Este mes"),
    "trimestre": _("Ultimos 3 meses"),
    "ano": _("Ultimos 12 meses"),
}


def default_range(codigo: str = "ano", *, hoy: date | None = None) -> tuple[date, date]:
    """Rango de fechas de un periodo con nombre."""
    hoy = hoy or timezone.localdate()
    if codigo == "mes":
        return month_start(hoy), hoy
    if codigo == "trimestre":
        inicio = month_start(hoy)
        for _repeticion in range(2):
            inicio = month_start(inicio - timedelta(days=1))
        return inicio, hoy
    inicio = month_start(hoy)
    for _repeticion in range(11):
        inicio = month_start(inicio - timedelta(days=1))
    return inicio, hoy
