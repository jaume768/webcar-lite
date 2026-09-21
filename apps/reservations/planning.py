"""Planning: las reservas pintadas sobre un calendario, un coche por fila.

Se agrupa por categoria y, dentro de cada una, una fila por vehiculo y una mas
para lo que aun no tiene coche asignado. Cada reserva es una barra que ocupa
los dias que dura. La geometria (columna de inicio, cuantos dias, en que carril)
se resuelve aqui: la plantilla solo coloca lo que recibe.

Dos consultas en total, una de coches y otra de reservas, sea cual sea el rango.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Reservation, ReservationStatus

#: Duraciones que ofrece el selector. Mas de un mes no se lee en una pantalla.
LONGITUDES = (7, 14, 31)
LONGITUD_POR_DEFECTO = 14

#: Lo que ocupa el calendario. Cancelada y no show no ocupan nada; el borrador
#: todavia no es una reserva.
ESTADOS_EN_PLANNING = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.IN_PROGRESS,
    ReservationStatus.FINISHED,
)

#: Color de cada barra. Cadenas completas: Tailwind solo genera lo que ve escrito.
ESTILO_DE_BARRA = {
    ReservationStatus.PENDING: "bg-amber-100 text-amber-900 ring-amber-300 hover:bg-amber-200",
    ReservationStatus.CONFIRMED: "bg-brand-100 text-brand-900 ring-brand-300 hover:bg-brand-200",
    ReservationStatus.IN_PROGRESS: (
        "bg-emerald-100 text-emerald-900 ring-emerald-300 hover:bg-emerald-200"
    ),
    ReservationStatus.FINISHED: "bg-slate-100 text-slate-600 ring-slate-300 hover:bg-slate-200",
}

LEYENDA = [
    (ReservationStatus.PENDING, _("Pendiente")),
    (ReservationStatus.CONFIRMED, _("Confirmada")),
    (ReservationStatus.IN_PROGRESS, _("En curso")),
    (ReservationStatus.FINISHED, _("Finalizada")),
]


@dataclass(frozen=True)
class PlanningDay:
    day: date
    is_today: bool

    @property
    def is_weekend(self) -> bool:
        return self.day.weekday() >= 5


@dataclass(frozen=True)
class PlanningBar:
    """Una reserva dentro de una fila: desde que columna, cuantas y en que carril."""

    reservation: Reservation
    start_col: int
    span: int
    lane: int
    #: La reserva empieza antes o acaba despues del rango que se ve.
    cut_start: bool
    cut_end: bool

    @property
    def style(self) -> str:
        return ESTILO_DE_BARRA.get(self.reservation.status, ESTILO_DE_BARRA["finished"])

    @property
    def url(self) -> str:
        return reverse("reservations:detail", args=[self.reservation.pk])

    @property
    def customer_name(self) -> str:
        cliente = self.reservation.customer
        return cliente.full_name if cliente else self.reservation.number


@dataclass
class PlanningRow:
    title: str
    subtitle: str = ""
    #: True en la fila de "sin asignar" de cada categoria.
    unassigned: bool = False
    bars: list = field(default_factory=list)

    @property
    def lanes(self) -> int:
        return max((barra.lane for barra in self.bars), default=1)


@dataclass
class PlanningGroup:
    category: object
    rows: list = field(default_factory=list)

    @property
    def reservations(self) -> int:
        return sum(len(fila.bars) for fila in self.rows)


@dataclass
class Planning:
    start: date
    length: int
    days: list
    groups: list

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.length - 1)

    @property
    def previous_start(self) -> date:
        return self.start - timedelta(days=self.length)

    @property
    def next_start(self) -> date:
        return self.start + timedelta(days=self.length)

    @property
    def total(self) -> int:
        return sum(grupo.reservations for grupo in self.groups)


def normalize_length(valor) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return LONGITUD_POR_DEFECTO
    return numero if numero in LONGITUDES else LONGITUD_POR_DEFECTO


def _repartir_en_carriles(barras: list[dict]) -> list[dict]:
    """Coloca las barras en carriles para que no se monten.

    En la fila de un coche no deberia haber solapes (lo impide la constraint de
    exclusion), pero en la de "sin asignar" es lo normal: varias reservas de la
    categoria esperando coche a la vez.
    """
    fin_de_carril: list[int] = []
    for barra in sorted(barras, key=lambda b: (b["start_col"], -b["span"])):
        ultima = barra["start_col"] + barra["span"] - 1
        for indice, fin in enumerate(fin_de_carril):
            if barra["start_col"] > fin:
                fin_de_carril[indice] = ultima
                barra["lane"] = indice + 1
                break
        else:
            fin_de_carril.append(ultima)
            barra["lane"] = len(fin_de_carril)
    return barras


def _barras(reservas, inicio: date, longitud: int) -> list[PlanningBar]:
    datos = []
    for reserva in reservas:
        desde = timezone.localdate(reserva.pickup_at)
        hasta = timezone.localdate(reserva.return_at)
        primera = max((desde - inicio).days, 0)
        ultima = min((hasta - inicio).days, longitud - 1)
        if ultima < 0 or primera > longitud - 1:
            continue
        datos.append(
            {
                "reservation": reserva,
                "start_col": primera + 1,
                "span": ultima - primera + 1,
                "cut_start": desde < inicio,
                "cut_end": (hasta - inicio).days > longitud - 1,
            }
        )
    return [PlanningBar(**dato) for dato in _repartir_en_carriles(datos)]


def build_planning(*, user, start: date, length: int, office=None, category=None) -> Planning:
    """Arma el planning con el scope de oficina del usuario.

    `office` y `category` recortan; una oficina que el usuario no tiene no
    ensena nada, igual que en el resto del sistema.
    """
    from apps.fleet.models import Vehicle, VehicleStatus
    from apps.offices.selectors import offices_for_user

    longitud = normalize_length(length)
    fin = start + timedelta(days=longitud - 1)
    hoy = timezone.localdate()
    dias = [
        PlanningDay(day=start + timedelta(days=n), is_today=start + timedelta(days=n) == hoy)
        for n in range(longitud)
    ]

    oficinas = offices_for_user(user)
    if office is not None:
        oficinas = oficinas.filter(pk=office.pk)
    office_ids = list(oficinas.values_list("pk", flat=True))

    reservas = (
        Reservation.objects.for_user(user)
        .filter(
            pickup_office_id__in=office_ids,
            status__in=ESTADOS_EN_PLANNING,
            pickup_at__date__lte=fin,
            return_at__date__gte=start,
        )
        .select_related("customer", "vehicle__category", "category")
        .order_by("pickup_at")
    )
    coches = (
        Vehicle.objects.filter(current_office_id__in=office_ids, is_active=True)
        .exclude(status=VehicleStatus.RETIRED)
        .select_related("category")
    )
    if category is not None:
        reservas = reservas.filter(category=category)
        coches = coches.filter(category=category)
    reservas = list(reservas)

    # Un coche de otra oficina (un one-way que aun no ha vuelto) tambien tiene
    # su fila si lleva una reserva de las que se ven.
    por_coche: dict[int, object] = {coche.pk: coche for coche in coches}
    for reserva in reservas:
        if reserva.vehicle_id and reserva.vehicle_id not in por_coche:
            por_coche[reserva.vehicle_id] = reserva.vehicle

    categorias: dict[int, object] = {}
    for coche in por_coche.values():
        categorias.setdefault(coche.category_id, coche.category)
    for reserva in reservas:
        categorias.setdefault(reserva.category_id, reserva.category)

    del_coche: dict[int, list] = defaultdict(list)
    sin_coche_por_categoria: dict[int, list] = defaultdict(list)
    for reserva in reservas:
        if reserva.vehicle_id:
            del_coche[reserva.vehicle_id].append(reserva)
        else:
            sin_coche_por_categoria[reserva.category_id].append(reserva)

    grupos = []
    for categoria in sorted(categorias.values(), key=lambda c: (c.sort_order, c.name)):
        grupo = PlanningGroup(category=categoria)
        de_la_categoria = sorted(
            (coche for coche in por_coche.values() if coche.category_id == categoria.pk),
            key=lambda coche: coche.plate,
        )
        for coche in de_la_categoria:
            grupo.rows.append(
                PlanningRow(
                    title=coche.plate,
                    subtitle=f"{coche.brand} {coche.model}",
                    bars=_barras(del_coche[coche.pk], start, longitud),
                )
            )
        # La consulta filtra por fecha en UTC y la barra por dia local: una
        # reserva que roza el borde del rango puede no dejar barra. Sin barras
        # no hay fila de "sin asignar" que ensenar.
        barras_sin_coche = _barras(sin_coche_por_categoria[categoria.pk], start, longitud)
        if barras_sin_coche:
            grupo.rows.append(
                PlanningRow(
                    title=str(_("Sin asignar")),
                    subtitle=str(_("Reservas de la categoría sin coche")),
                    unassigned=True,
                    bars=barras_sin_coche,
                )
            )
        grupos.append(grupo)

    return Planning(start=start, length=longitud, days=dias, groups=grupos)
