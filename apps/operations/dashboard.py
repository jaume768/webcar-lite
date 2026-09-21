"""Datos del panel de mostrador.

Todo el panel se arma con un punado de consultas contadas: la pantalla se abre
decenas de veces al dia y con datos de un ano entero. Nada de recorrer reservas
preguntando cosas de una en una.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from apps.billing.models import DEPOSIT_TYPES, RENTAL_TYPES, Payment, PaymentMethod
from apps.billing.selectors import annotate_balance
from apps.fleet.models import Vehicle, VehicleStatus
from apps.reservations.models import (
    CAPACITY_CONSUMING_STATUSES,
    FINAL_STATUSES,
    Reservation,
    ReservationStatus,
)

CERO = Decimal("0.00")

#: Dias de aviso para ITV y seguro.
DIAS_DE_AVISO = 30

#: Como mucho se pintan estas filas por bloque: el panel es para trabajar, no
#: para leer el ano entero.
LIMITE_POR_BLOQUE = 50

#: Dias que abarca la prevision de ocupacion.
DIAS_DE_PREVISION = 14

#: Estados de una entrega que aun no se ha hecho.
POR_ENTREGAR = (ReservationStatus.PENDING, ReservationStatus.CONFIRMED)


# ---------------------------------------------------------------------------
# Periodo que mira el panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    code: str
    label: str
    #: Complemento de los indicadores: "Entregas hoy", "Entregas esta semana".
    suffix: str
    first_offset: int
    last_offset: int
    #: Solo en el periodo de un dia elegido en el calendario.
    day: date | None = None

    def bounds(self, today: date) -> tuple[date, date]:
        return today + timedelta(days=self.first_offset), today + timedelta(days=self.last_offset)

    @property
    def is_today(self) -> bool:
        return self.first_offset == self.last_offset == 0

    @property
    def is_past(self) -> bool:
        """Un dia que ya paso: lo que salio o volvio ya esta finalizado."""
        return self.last_offset < 0

    def anchor(self, today: date) -> date:
        """Dia de referencia del selector de fecha: el primero del periodo."""
        return self.day or today + timedelta(days=self.first_offset)

    @property
    def query(self) -> str:
        """Parametro de la URL que vuelve a pedir este mismo periodo."""
        return f"dia={self.day.isoformat()}" if self.day else f"periodo={self.code}"

    @classmethod
    def for_day(cls, day: date, today: date) -> "Period":
        desfase = (day - today).days
        return cls(
            code="dia",
            label=date_format(day, "j M Y"),
            suffix=str(_("el %(dia)s") % {"dia": date_format(day, "j \\d\\e F")}),
            first_offset=desfase,
            last_offset=desfase,
            day=day,
        )


PERIODS = {
    "hoy": Period("hoy", _("Hoy"), _("hoy"), 0, 0),
    "manana": Period("manana", _("Mañana"), _("mañana"), 1, 1),
    "semana": Period("semana", _("Semana"), _("esta semana"), 0, 6),
}

#: Hasta donde se puede saltar con el selector de dia, en cada sentido. Evita
#: que una fecha absurda en la URL dispare consultas sobre decadas de datos.
MAXIMO_DIAS_DE_SALTO = 3 * 365


def period_for(code: str | None, day: str | None = None, *, today: date | None = None) -> Period:
    """Periodo pedido por la URL. Lo que no se reconoce cae en hoy.

    `day` (AAAA-MM-DD) gana a `code`: es el dia elegido en el calendario. Si
    coincide con hoy o manana se devuelve ese periodo, para que su pestana
    salga marcada.
    """
    hoy = today or timezone.localdate()
    if day:
        try:
            elegido = date.fromisoformat(day)
        except ValueError:
            elegido = None
        if elegido is not None and abs((elegido - hoy).days) <= MAXIMO_DIAS_DE_SALTO:
            if elegido == hoy:
                return PERIODS["hoy"]
            if elegido == hoy + timedelta(days=1):
                return PERIODS["manana"]
            return Period.for_day(elegido, hoy)
    return PERIODS.get(code or "", PERIODS["hoy"])


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------

# Los colores van como cadenas completas y no compuestas a trozos en la
# plantilla: Tailwind solo genera las clases que encuentra escritas tal cual, y
# `bg-{{ tono }}-50` no existiria en el CSS. Es el mismo criterio que
# `core.badges`, y el CSS escanea tambien los .py justo por esto.
#
# El color no es adorno: en una pantalla que se mira de reojo entre cliente y
# cliente, es lo que separa "hay que resolverlo ya" de "mirarlo esta semana".
ESTILO_DE_ALERTA = {
    "sin_vehiculo": {"tile": "bg-amber-50 text-amber-600", "icon": "coche"},
    "documentacion": {"tile": "bg-sky-50 text-sky-600", "icon": "llave"},
    "retraso": {"tile": "bg-rose-50 text-rose-600", "icon": "reloj"},
    "sin_cobrar": {"tile": "bg-violet-50 text-violet-600", "icon": "tarjeta"},
    "fianzas": {"tile": "bg-orange-50 text-orange-600", "icon": "aviso"},
}

ESTILO_NEUTRO = {"tile": "bg-slate-100 text-slate-600", "icon": "aviso"}

#: Color de cada estado de flota, para leer la fila de un vistazo.
ESTILO_DE_ESTADO = {
    VehicleStatus.AVAILABLE: {"dot": "bg-emerald-500", "text": "text-emerald-700"},
    VehicleStatus.RESERVED: {"dot": "bg-sky-500", "text": "text-sky-700"},
    VehicleStatus.RENTED: {"dot": "bg-brand-500", "text": "text-brand-700"},
    VehicleStatus.WORKSHOP: {"dot": "bg-amber-500", "text": "text-amber-700"},
    VehicleStatus.CLEANING: {"dot": "bg-violet-500", "text": "text-violet-700"},
    VehicleStatus.BLOCKED: {"dot": "bg-rose-500", "text": "text-rose-700"},
    VehicleStatus.RETIRED: {"dot": "bg-slate-300", "text": "text-slate-600"},
}

#: Estado de cada linea de la agenda del dia.
ESTILO_DE_AGENDA = {
    "hecho": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "en_curso": "bg-brand-50 text-brand-700 ring-brand-600/20",
    "pendiente": "bg-amber-50 text-amber-700 ring-amber-600/20",
    "sin_coche": "bg-rose-50 text-rose-700 ring-rose-600/20",
    "retraso": "bg-rose-50 text-rose-700 ring-rose-600/20",
}

#: Color de cada medio de pago en el grafico de caja (trazo SVG y punto).
ESTILO_DE_MEDIO = {
    PaymentMethod.CARD: {"stroke": "stroke-brand-600", "dot": "bg-brand-600"},
    PaymentMethod.CASH: {"stroke": "stroke-sky-500", "dot": "bg-sky-500"},
    PaymentMethod.TRANSFER: {"stroke": "stroke-brand-300", "dot": "bg-brand-300"},
    PaymentMethod.DATAPHONE: {"stroke": "stroke-indigo-400", "dot": "bg-indigo-400"},
    PaymentMethod.PAYPAL: {"stroke": "stroke-cyan-400", "dot": "bg-cyan-400"},
    PaymentMethod.OTHER: {"stroke": "stroke-slate-400", "dot": "bg-slate-400"},
}


def _porcentaje(parte, total) -> int:
    """Porcentaje entero para pintar. Es un indicador, no contabilidad."""
    if not total:
        return 0
    return round(parte * 100 / total)


# ---------------------------------------------------------------------------
# Piezas del panel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alert:
    kind: str
    label: str
    count: int
    detail: str = ""
    url: str = ""

    @property
    def style(self) -> dict:
        return ESTILO_DE_ALERTA.get(self.kind, ESTILO_NEUTRO)

    def __bool__(self) -> bool:
        return self.count > 0


@dataclass(frozen=True)
class Stats:
    active_reservations: int = 0
    created_today: int = 0
    pickups_today: int = 0
    pickups_done: int = 0
    returns_today: int = 0
    pending_amount: Decimal = CERO
    pending_reservations: int = 0
    fleet_total: int = 0
    fleet_by_status: dict = field(default_factory=dict)

    @property
    def pickups_total(self) -> int:
        """Entregas del periodo, hechas o no: el "de 12" del indicador."""
        return self.pickups_today + self.pickups_done

    @property
    def fleet_rows(self) -> list[tuple[str, int, dict, int]]:
        """Estados de flota con recuento, color y porcentaje, listos para pintar.

        Se arma aqui y no en la plantilla para no necesitar un filtro generico
        que busque en un diccionario por clave variable.
        """
        return [
            (
                str(etiqueta),
                self.fleet_by_status.get(valor, 0),
                ESTILO_DE_ESTADO.get(valor, ESTILO_DE_ESTADO[VehicleStatus.RETIRED]),
                _porcentaje(self.fleet_by_status.get(valor, 0), self.fleet_total),
            )
            for valor, etiqueta in VehicleStatus.choices
        ]

    @property
    def rented(self) -> int:
        return self.fleet_by_status.get(VehicleStatus.RENTED, 0)

    @property
    def available(self) -> int:
        return self.fleet_by_status.get(VehicleStatus.AVAILABLE, 0)

    @property
    def occupancy(self) -> int:
        """Porcentaje de flota fuera."""
        return _porcentaje(self.rented, self.fleet_total)

    @property
    def occupancy_ring(self) -> str:
        """`stroke-dasharray` del anillo de ocupacion (circunferencia de 100)."""
        return f"{self.occupancy} {100 - self.occupancy}"


@dataclass(frozen=True)
class AgendaItem:
    """Una linea de la agenda: una entrega o una devolucion del periodo."""

    at: datetime
    title: str
    detail: str
    state: str
    state_label: str
    url: str

    @property
    def state_style(self) -> str:
        return ESTILO_DE_AGENDA.get(self.state, ESTILO_DE_AGENDA["pendiente"])


#: Inicial de cada dia de la semana, de lunes a domingo. El miercoles es la X,
#: como en cualquier calendario espanol: la M ya es del martes.
INICIALES_DE_DIA = (_("L"), _("M"), _("X"), _("J"), _("V"), _("S"), _("D"))


@dataclass(frozen=True)
class DayOccupancy:
    day: date
    reservations: int
    percent: int

    @property
    def is_weekend(self) -> bool:
        return self.day.weekday() >= 5

    @property
    def initial(self) -> str:
        return str(INICIALES_DE_DIA[self.day.weekday()])


# Medidas del grafico de prevision, en unidades del viewBox del SVG.
ANCHO_GRAFICO = 380
ALTO_GRAFICO = 150


@dataclass(frozen=True)
class OccupancyForecast:
    """Ocupacion prevista dia a dia, con la geometria del grafico ya resuelta.

    Las coordenadas se calculan aqui porque las plantillas no hacen cuentas; la
    plantilla solo coloca lo que recibe.
    """

    days: list = field(default_factory=list)
    capacity: int = 0

    @property
    def width(self) -> int:
        return ANCHO_GRAFICO

    @property
    def height(self) -> int:
        return ALTO_GRAFICO

    @property
    def _paso(self) -> float:
        return ANCHO_GRAFICO / max(len(self.days) - 1, 1)

    def _y(self, porcentaje: int) -> float:
        return round(ALTO_GRAFICO - porcentaje * ALTO_GRAFICO / 100, 1)

    @property
    def points(self) -> list[dict]:
        return [
            {"x": round(i * self._paso, 1), "y": self._y(dia.percent), "day": dia}
            for i, dia in enumerate(self.days)
        ]

    @property
    def line(self) -> str:
        return " ".join(f"{p['x']},{p['y']}" for p in self.points)

    @property
    def area(self) -> str:
        if not self.points:
            return ""
        return (
            f"M0,{ALTO_GRAFICO} L{self.line.replace(' ', ' L')} L{ANCHO_GRAFICO},{ALTO_GRAFICO} Z"
        )

    @property
    def weekend_bands(self) -> list[dict]:
        """Franja de cada fin de semana, centrada en sus dias."""
        return [
            {"x": max(p["x"] - self._paso / 2, 0), "width": self._paso}
            for p in self.points
            if p["day"].is_weekend
        ]

    @property
    def gridlines(self) -> list[dict]:
        return [{"value": v, "y": self._y(v)} for v in (100, 75, 50, 25, 0)]

    @property
    def labels(self) -> list[dict]:
        """Una fecha de cada dos: con catorce seguidas no se lee ninguna."""
        return [p for i, p in enumerate(self.points) if i % 2 == 0]

    @property
    def week(self) -> list:
        """Los siete primeros dias: las barras de la semana en el movil."""
        return self.days[:7]

    @property
    def peak(self) -> DayOccupancy | None:
        return max(self.days, key=lambda dia: dia.percent, default=None)


@dataclass(frozen=True)
class MethodTotal:
    label: str
    amount: Decimal
    percent: int
    style: dict
    #: Desplazamiento del trazo en el anillo: cada tramo empieza donde acaba el
    #: anterior, contando desde arriba (25 en una circunferencia de 100).
    offset: int = 25

    @property
    def dasharray(self) -> str:
        return f"{self.percent} {100 - self.percent}"


@dataclass(frozen=True)
class CashSummary:
    """Caja del dia en las oficinas del panel. Solo con permiso de cobros."""

    collected: Decimal = CERO
    pending: Decimal = CERO
    deposits_held: Decimal = CERO
    by_method: list = field(default_factory=list)


@dataclass(frozen=True)
class Dashboard:
    office: object | None
    offices: list
    show_office_picker: bool
    pickups: list
    returns: list
    alerts: list
    stats: Stats
    generated_at: object
    period: Period = PERIODS["hoy"]
    agenda: list = field(default_factory=list)
    forecast: OccupancyForecast = field(default_factory=OccupancyForecast)
    cash: CashSummary | None = None


# ---------------------------------------------------------------------------
# Armado
# ---------------------------------------------------------------------------


def _oficinas_en_alcance(permitidas, office=None) -> list[int]:
    """Ids de las oficinas sobre las que mira el panel.

    Con una oficina elegida, esa; si no, todas las del usuario. El scope manda
    siempre: elegir una oficina ajena por la URL no ensena nada nuevo.

    Recibe las oficinas ya resueltas para no volver a preguntarlas.
    """
    ids = [oficina.pk for oficina in permitidas]
    if office is not None:
        return [office.pk] if office.pk in ids else []
    return ids


def _reservas_base(office_ids):
    return (
        Reservation.objects.filter(pickup_office_id__in=office_ids)
        .select_related("customer", "category", "vehicle", "pickup_office", "return_office")
        .order_by("pickup_at")
    )


def _coche(reserva) -> str:
    if reserva.vehicle_id:
        return f"{reserva.vehicle.brand} {reserva.vehicle.model}"
    return reserva.category.name


def _agenda(entregas, devoluciones, ahora) -> list[AgendaItem]:
    """Entregas y devoluciones en una sola linea de tiempo. Sin consultas."""
    lineas = []
    for reserva in entregas:
        cliente = reserva.customer.full_name if reserva.customer_id else reserva.number
        if reserva.status not in POR_ENTREGAR:
            estado, etiqueta = "hecho", _("Entregado")
        elif reserva.vehicle_id is None:
            estado, etiqueta = "sin_coche", _("Sin coche")
        else:
            estado, etiqueta = "pendiente", _("Pendiente")
        lineas.append(
            AgendaItem(
                at=reserva.pickup_at,
                title=str(_("Entrega de %(coche)s") % {"coche": _coche(reserva)}),
                detail=" · ".join(
                    filter(None, [cliente, reserva.vehicle.plate if reserva.vehicle_id else ""])
                ),
                state=estado,
                state_label=str(etiqueta),
                url=reverse("reservations:detail", args=[reserva.pk]),
            )
        )
    for reserva in devoluciones:
        cliente = reserva.customer.full_name if reserva.customer_id else reserva.number
        if reserva.status == ReservationStatus.IN_PROGRESS and reserva.return_at < ahora:
            estado, etiqueta = "retraso", _("Con retraso")
        elif reserva.status == ReservationStatus.IN_PROGRESS:
            estado, etiqueta = "en_curso", _("En curso")
        else:
            estado, etiqueta = "pendiente", _("Prevista")
        lineas.append(
            AgendaItem(
                at=reserva.return_at,
                title=str(_("Devolución de %(coche)s") % {"coche": _coche(reserva)}),
                detail=" · ".join(
                    filter(None, [cliente, reserva.vehicle.plate if reserva.vehicle_id else ""])
                ),
                state=estado,
                state_label=str(etiqueta),
                url=reverse("reservations:detail", args=[reserva.pk]),
            )
        )
    return sorted(lineas, key=lambda linea: linea.at)


def _prevision(office_ids, hoy, capacidad) -> OccupancyForecast:
    """Ocupacion de los proximos dias: reservas vivas que pisan cada dia.

    Una sola consulta acotada a la ventana: lo que acabo antes de hoy o empieza
    despues del ultimo dia no se trae.
    """
    ultimo = hoy + timedelta(days=DIAS_DE_PREVISION - 1)
    tramos = [
        (timezone.localdate(inicio), timezone.localdate(fin))
        for inicio, fin in Reservation.objects.filter(
            pickup_office_id__in=office_ids,
            status__in=CAPACITY_CONSUMING_STATUSES,
            pickup_at__date__lte=ultimo,
            return_at__date__gte=hoy,
        ).values_list("pickup_at", "return_at")
    ]
    dias = []
    for n in range(DIAS_DE_PREVISION):
        dia = hoy + timedelta(days=n)
        ocupados = sum(1 for inicio, fin in tramos if inicio <= dia <= fin)
        dias.append(
            DayOccupancy(
                day=dia,
                reservations=ocupados,
                percent=min(_porcentaje(ocupados, capacidad), 100),
            )
        )
    return OccupancyForecast(days=dias, capacity=capacidad)


def _caja(office_ids, hoy, pendiente, fianzas) -> CashSummary:
    """Cobrado hoy por medio de pago. Una consulta agrupada, sin recorrer cobros.

    Solo cuentan los cobros del alquiler: la fianza no es un ingreso.
    """
    filas = (
        Payment.objects.filter(office_id__in=office_ids)
        .on_day(hoy)
        .filter(payment_type__in=RENTAL_TYPES)
        .values("method")
        .annotate(suma=Sum("amount"))
        .order_by()
    )
    por_medio = {fila["method"]: fila["suma"] for fila in filas}
    cobrado = sum(por_medio.values(), CERO)

    positivos = [
        (valor, etiqueta)
        for valor, etiqueta in PaymentMethod.choices
        if por_medio.get(valor, CERO) > 0
    ]
    base = sum((por_medio[valor] for valor, _e in positivos), CERO)
    tramos = []
    acumulado = 0
    for valor, etiqueta in positivos:
        porcentaje = _porcentaje(por_medio[valor], base)
        tramos.append(
            MethodTotal(
                label=str(etiqueta),
                amount=por_medio[valor],
                percent=porcentaje,
                style=ESTILO_DE_MEDIO.get(valor, ESTILO_DE_MEDIO[PaymentMethod.OTHER]),
                offset=25 - acumulado,
            )
        )
        acumulado += porcentaje
    return CashSummary(
        collected=cobrado, pending=pendiente, deposits_held=fianzas, by_method=tramos
    )


def build_dashboard(*, user, office=None, now=None, period=None, include_cash=False) -> Dashboard:
    """Arma el panel entero. Una consulta por bloque, ni una mas.

    `include_cash` lo decide quien llama segun los permisos del usuario: la
    caja del dia no se consulta para quien no puede verla.
    """
    from apps.offices.selectors import offices_for_user

    periodo = period or PERIODS["hoy"]
    ahora = now or timezone.now()
    hoy = timezone.localtime(ahora).date()
    desde, hasta = periodo.bounds(hoy)

    permitidas = list(offices_for_user(user).order_by("name"))
    office_ids = _oficinas_en_alcance(permitidas, office)

    if not office_ids:
        return Dashboard(
            office=office,
            offices=permitidas,
            show_office_picker=len(permitidas) > 1,
            pickups=[],
            returns=[],
            alerts=[],
            stats=Stats(),
            generated_at=ahora,
            period=periodo,
        )

    # --- entregas y devoluciones del periodo --------------------------------
    # Las entregas ya hechas tambien salen: el mostrador quiere ver como va el
    # dia, no solo lo que queda.
    # En un dia pasado lo que salio y ya volvio esta finalizado: tambien cuenta.
    ya_cerradas = (ReservationStatus.FINISHED,) if periodo.is_past else ()
    entregas = list(
        annotate_balance(
            _reservas_base(office_ids).filter(
                status__in=(*POR_ENTREGAR, ReservationStatus.IN_PROGRESS, *ya_cerradas),
                pickup_at__date__range=(desde, hasta),
            )
        )[:LIMITE_POR_BLOQUE]
    )
    por_entregar = [reserva for reserva in entregas if reserva.status in POR_ENTREGAR]

    # Hoy solo vuelve lo que esta fuera. Mirando hacia delante tambien cuenta lo
    # que aun no ha salido pero volvera dentro del periodo.
    if periodo.is_today:
        estados_de_vuelta = (ReservationStatus.IN_PROGRESS,)
    elif periodo.is_past:
        estados_de_vuelta = (ReservationStatus.IN_PROGRESS, ReservationStatus.FINISHED)
    else:
        estados_de_vuelta = (*POR_ENTREGAR, ReservationStatus.IN_PROGRESS)
    devoluciones = list(
        annotate_balance(
            Reservation.objects.filter(
                return_office_id__in=office_ids,
                status__in=estados_de_vuelta,
                return_at__date__range=(desde, hasta),
            )
            .select_related("customer", "category", "vehicle", "return_office")
            .order_by("return_at")
        )[:LIMITE_POR_BLOQUE]
    )

    # --- alertas ------------------------------------------------------------
    # Las entregas sin coche salen de la lista que ya tenemos: no cuesta consulta.
    sin_vehiculo = [reserva for reserva in por_entregar if reserva.vehicle_id is None]

    caducan = list(
        Vehicle.objects.filter(current_office_id__in=office_ids, is_active=True)
        .exclude(status=VehicleStatus.RETIRED)
        .filter(
            Q(itv_expiry__lte=hoy + timedelta(days=DIAS_DE_AVISO))
            | Q(insurance_expiry__lte=hoy + timedelta(days=DIAS_DE_AVISO))
        )
        .select_related("current_office")
        .order_by("itv_expiry", "insurance_expiry")[:LIMITE_POR_BLOQUE]
    )

    con_retraso = list(
        Reservation.objects.filter(
            return_office_id__in=office_ids,
            status=ReservationStatus.IN_PROGRESS,
            return_at__lt=ahora,
        )
        .select_related("customer", "vehicle", "return_office")
        .order_by("return_at")[:LIMITE_POR_BLOQUE]
    )

    sin_cobrar = list(
        annotate_balance(
            Reservation.objects.filter(
                pickup_office_id__in=office_ids, status=ReservationStatus.FINISHED
            ).select_related("customer", "pickup_office")
        )
        .filter(pendiente__gt=0)
        .order_by("-return_at")[:LIMITE_POR_BLOQUE]
    )

    # Fianza retenida por reserva: cobrada menos devuelta. Se agrupa en la base
    # y solo vuelven las reservas que aun tienen dinero retenido.
    retenidas = list(
        Payment.objects.filter(
            payment_type__in=DEPOSIT_TYPES, reservation__pickup_office_id__in=office_ids
        )
        .values("reservation_id", "reservation__number", "reservation__status")
        .annotate(retenido=Sum("amount"))
        .filter(retenido__gt=0)
        .order_by()
    )
    fianzas_total = sum((fila["retenido"] for fila in retenidas), CERO)
    # Una fianza de una reserva cerrada ya deberia haberse devuelto.
    por_devolver = [fila for fila in retenidas if fila["reservation__status"] in FINAL_STATUSES]
    por_devolver_total = sum((fila["retenido"] for fila in por_devolver), CERO)

    lista = reverse("reservations:list")
    alertas = [
        Alert(
            kind="sin_vehiculo",
            label=str(_("Entregas %(periodo)s sin coche asignado") % {"periodo": periodo.suffix}),
            count=len(sin_vehiculo),
            detail=", ".join(reserva.number for reserva in sin_vehiculo[:5]),
            url=lista,
        ),
        Alert(
            kind="documentacion",
            label=str(_("ITV o seguro que caducan en 30 dias")),
            count=len(caducan),
            detail=", ".join(vehiculo.plate for vehiculo in caducan[:5]),
            url=reverse("fleet:vehicle_list"),
        ),
        Alert(
            kind="retraso",
            label=str(_("Devoluciones con retraso")),
            count=len(con_retraso),
            detail=", ".join(reserva.number for reserva in con_retraso[:5]),
            url=f"{lista}?status={ReservationStatus.IN_PROGRESS}",
        ),
        Alert(
            kind="sin_cobrar",
            label=str(_("Finalizadas con cobro pendiente")),
            count=len(sin_cobrar),
            detail=", ".join(reserva.number for reserva in sin_cobrar[:5]),
            url=f"{lista}?pendientes=true",
        ),
        Alert(
            kind="fianzas",
            label=str(
                _("Fianzas retenidas por devolver · %(importe)s €")
                % {"importe": por_devolver_total}
            ),
            count=len(por_devolver),
            detail=", ".join(fila["reservation__number"] for fila in por_devolver[:5]),
            url=lista,
        ),
    ]

    # --- indicadores --------------------------------------------------------
    inicio_de_hoy = timezone.make_aware(datetime.combine(hoy, datetime.min.time()))
    reservas = Reservation.objects.filter(pickup_office_id__in=office_ids).aggregate(
        activas=Count("pk", filter=Q(status__in=CAPACITY_CONSUMING_STATUSES)),
        creadas_hoy=Count(
            "pk",
            filter=Q(created_at__gte=inicio_de_hoy) & ~Q(status=ReservationStatus.DRAFT),
        ),
    )

    flota = dict(
        Vehicle.objects.filter(current_office_id__in=office_ids, is_active=True)
        .values_list("status")
        .annotate(total=Count("id"))
    )

    pendiente = annotate_balance(
        Reservation.objects.filter(
            pickup_office_id__in=office_ids,
            status__in=(*CAPACITY_CONSUMING_STATUSES, ReservationStatus.FINISHED),
        )
    ).aggregate(suma=Sum("pendiente"), reservas=Count("pk", filter=Q(pendiente__gt=0)))

    stats = Stats(
        active_reservations=reservas["activas"],
        created_today=reservas["creadas_hoy"],
        pickups_today=len(por_entregar),
        pickups_done=len(entregas) - len(por_entregar),
        returns_today=len(devoluciones),
        pending_amount=pendiente["suma"] or CERO,
        pending_reservations=pendiente["reservas"],
        fleet_total=sum(flota.values()),
        fleet_by_status=flota,
    )

    # La capacidad es la flota que puede salir: lo dado de baja no cuenta.
    capacidad = stats.fleet_total - flota.get(VehicleStatus.RETIRED, 0)

    return Dashboard(
        office=office,
        offices=permitidas,
        # Con una sola oficina no hay nada que elegir: el selector sobra.
        show_office_picker=len(permitidas) > 1,
        pickups=entregas,
        returns=devoluciones,
        alerts=[alerta for alerta in alertas if alerta],
        stats=stats,
        generated_at=ahora,
        period=periodo,
        agenda=_agenda(entregas, devoluciones, ahora),
        forecast=_prevision(office_ids, hoy, capacidad),
        cash=_caja(office_ids, hoy, stats.pending_amount, fianzas_total) if include_cash else None,
    )
