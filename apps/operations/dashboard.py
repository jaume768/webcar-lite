"""Datos del panel de mostrador.

Todo el panel se arma con un punado de consultas contadas: la pantalla se abre
decenas de veces al dia y con datos de un ano entero. Nada de recorrer reservas
preguntando cosas de una en una.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.billing.selectors import annotate_balance
from apps.fleet.models import Vehicle, VehicleStatus
from apps.reservations.models import CAPACITY_CONSUMING_STATUSES, Reservation, ReservationStatus

CERO = Decimal("0.00")

#: Dias de aviso para ITV y seguro.
DIAS_DE_AVISO = 30

#: Como mucho se pintan estas filas por bloque: el panel es para trabajar, no
#: para leer el ano entero.
LIMITE_POR_BLOQUE = 50


# Los colores van como cadenas completas y no compuestas a trozos en la
# plantilla: Tailwind solo genera las clases que encuentra escritas tal cual, y
# `bg-{{ tono }}-50` no existiria en el CSS. Es el mismo criterio que
# `core.badges`, y el CSS escanea tambien los .py justo por esto.
#
# El color no es adorno: en una pantalla que se mira de reojo entre cliente y
# cliente, es lo que separa "hay que resolverlo ya" de "mirarlo esta semana".
ESTILO_DE_ALERTA = {
    "sin_vehiculo": {
        "card": "border-l-amber-500 bg-amber-50/50",
        "count": "text-amber-700",
        "label": "text-amber-900",
        "detail": "text-amber-800/80",
    },
    "documentacion": {
        "card": "border-l-sky-500 bg-sky-50/50",
        "count": "text-sky-700",
        "label": "text-sky-900",
        "detail": "text-sky-800/80",
    },
    "retraso": {
        "card": "border-l-rose-500 bg-rose-50/50",
        "count": "text-rose-700",
        "label": "text-rose-900",
        "detail": "text-rose-800/80",
    },
    "sin_cobrar": {
        "card": "border-l-violet-500 bg-violet-50/50",
        "count": "text-violet-700",
        "label": "text-violet-900",
        "detail": "text-violet-800/80",
    },
}

ESTILO_NEUTRO = {
    "card": "border-l-slate-400 bg-slate-50/50",
    "count": "text-slate-700",
    "label": "text-slate-900",
    "detail": "text-slate-600",
}

#: Color de cada estado de flota, para leer la fila de un vistazo.
ESTILO_DE_ESTADO = {
    VehicleStatus.AVAILABLE: {
        "dot": "bg-emerald-500",
        "top": "border-t-emerald-500",
        "text": "text-emerald-700",
    },
    VehicleStatus.RESERVED: {
        "dot": "bg-sky-500",
        "top": "border-t-sky-500",
        "text": "text-sky-700",
    },
    VehicleStatus.RENTED: {
        "dot": "bg-brand-500",
        "top": "border-t-brand-500",
        "text": "text-brand-700",
    },
    VehicleStatus.WORKSHOP: {
        "dot": "bg-amber-500",
        "top": "border-t-amber-500",
        "text": "text-amber-700",
    },
    VehicleStatus.CLEANING: {
        "dot": "bg-violet-500",
        "top": "border-t-violet-500",
        "text": "text-violet-700",
    },
    VehicleStatus.BLOCKED: {
        "dot": "bg-rose-500",
        "top": "border-t-rose-500",
        "text": "text-rose-700",
    },
    VehicleStatus.RETIRED: {
        "dot": "bg-slate-400",
        "top": "border-t-slate-400",
        "text": "text-slate-600",
    },
}


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
    pickups_today: int = 0
    returns_today: int = 0
    pending_amount: Decimal = CERO
    fleet_total: int = 0
    fleet_by_status: dict = field(default_factory=dict)

    @property
    def fleet_rows(self) -> list[tuple[str, int, dict]]:
        """Estados de flota con su recuento y su color, listos para pintar.

        Se arma aqui y no en la plantilla para no necesitar un filtro generico
        que busque en un diccionario por clave variable.
        """
        return [
            (
                str(etiqueta),
                self.fleet_by_status.get(valor, 0),
                ESTILO_DE_ESTADO.get(valor, ESTILO_DE_ESTADO[VehicleStatus.RETIRED]),
            )
            for valor, etiqueta in VehicleStatus.choices
        ]

    @property
    def rented(self) -> int:
        return self.fleet_by_status.get(VehicleStatus.RENTED, 0)

    @property
    def occupancy(self) -> int:
        """Porcentaje de flota fuera. Redondeado: es un indicador, no contabilidad."""
        if not self.fleet_total:
            return 0
        return round(self.rented * 100 / self.fleet_total)


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


def build_dashboard(*, user, office=None, now=None) -> Dashboard:
    """Arma el panel entero. Una consulta por bloque, ni una mas."""
    from apps.offices.selectors import offices_for_user

    ahora = now or timezone.now()
    hoy = timezone.localtime(ahora).date()

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
        )

    # --- entregas y devoluciones de hoy ------------------------------------
    entregas = list(
        annotate_balance(
            _reservas_base(office_ids).filter(
                status__in=(ReservationStatus.PENDING, ReservationStatus.CONFIRMED),
                pickup_at__date=hoy,
            )
        )[:LIMITE_POR_BLOQUE]
    )

    devoluciones = list(
        annotate_balance(
            Reservation.objects.filter(
                return_office_id__in=office_ids,
                status=ReservationStatus.IN_PROGRESS,
                return_at__date=hoy,
            )
            .select_related("customer", "category", "vehicle", "return_office")
            .order_by("return_at")
        )[:LIMITE_POR_BLOQUE]
    )

    # --- alertas ------------------------------------------------------------
    # Las de hoy sin coche salen de la lista que ya tenemos: no cuesta consulta.
    sin_vehiculo = [reserva for reserva in entregas if reserva.vehicle_id is None]

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

    alertas = [
        Alert(
            kind="sin_vehiculo",
            label=str(_("Entregas de hoy sin coche asignado")),
            count=len(sin_vehiculo),
            detail=", ".join(reserva.number for reserva in sin_vehiculo[:5]),
        ),
        Alert(
            kind="documentacion",
            label=str(_("ITV o seguro que caducan en 30 dias")),
            count=len(caducan),
            detail=", ".join(vehiculo.plate for vehiculo in caducan[:5]),
        ),
        Alert(
            kind="retraso",
            label=str(_("Devoluciones con retraso")),
            count=len(con_retraso),
            detail=", ".join(reserva.number for reserva in con_retraso[:5]),
        ),
        Alert(
            kind="sin_cobrar",
            label=str(_("Finalizadas con cobro pendiente")),
            count=len(sin_cobrar),
            detail=", ".join(reserva.number for reserva in sin_cobrar[:5]),
        ),
    ]

    # --- indicadores --------------------------------------------------------
    activas = Reservation.objects.filter(
        pickup_office_id__in=office_ids, status__in=CAPACITY_CONSUMING_STATUSES
    ).count()

    flota = dict(
        Vehicle.objects.filter(current_office_id__in=office_ids, is_active=True)
        .values_list("status")
        .annotate(total=Count("id"))
    )

    pendiente_total = (
        annotate_balance(
            Reservation.objects.filter(
                pickup_office_id__in=office_ids,
                status__in=(*CAPACITY_CONSUMING_STATUSES, ReservationStatus.FINISHED),
            )
        ).aggregate(suma=Sum("pendiente"))["suma"]
        or CERO
    )

    stats = Stats(
        active_reservations=activas,
        pickups_today=len(entregas),
        returns_today=len(devoluciones),
        pending_amount=pendiente_total,
        fleet_total=sum(flota.values()),
        fleet_by_status=flota,
    )

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
    )
