"""Resolucion de temporada, tarifa y tramo.

Elegir tarifa es la parte del motor donde es facil hacer trampa: cuando dos
tarifas encajan igual de bien, lo comodo es coger la primera. Aqui no. Se
ordena por prioridad y por especificidad, y si sigue habiendo empate se lanza
`AmbiguousRate` con los codigos que empatan, para que alguien lo arregle en los
datos en vez de vender a un precio elegido al azar.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from ..models import Rate, Season, TierMode


class PricingError(ServiceError):
    """Base de los errores del motor de tarifas."""


class NoRateAvailable(PricingError):
    """No hay ninguna tarifa aplicable.

    Nunca se devuelve precio 0 en su lugar: un cero silencioso acaba en un
    contrato firmado a cero euros.
    """


class AmbiguousRate(PricingError):
    """Dos tarifas encajan igual de bien. Lo arregla quien mantiene los datos."""


class AmbiguousSeason(PricingError):
    """Dos temporadas cubren la fecha con la misma prioridad."""


class NoTierAvailable(PricingError):
    """La tarifa no tiene un tramo para esa duracion."""


# ---------------------------------------------------------------------------
# Temporada
# ---------------------------------------------------------------------------


def resolve_season(fecha) -> Season | None:
    """Temporada que manda en una fecha, o None si no hay ninguna.

    **Regla del sistema: manda la fecha de recogida.** Una reserva que cruza el
    cambio de temporada se cobra entera a la temporada en la que empieza. Es lo
    que espera el cliente (le has dado un precio al reservar) y lo que hace el
    sector; partir el alquiler en dos mitades daria un precio que nadie sabe
    explicar en el mostrador.

    Las temporadas pueden solaparse; gana la de mayor prioridad. Si dos empatan
    en prioridad, `AmbiguousSeason`: no se elige a ciegas.
    """
    candidatas = list(
        Season.objects.active()
        .filter(start_date__lte=fecha, end_date__gte=fecha)
        .order_by("-priority", "start_date")
    )
    if not candidatas:
        return None

    ganadora = candidatas[0]
    empatadas = [s for s in candidatas if s.priority == ganadora.priority]
    if len(empatadas) > 1:
        raise AmbiguousSeason(
            str(
                _("Dos temporadas cubren el %(fecha)s con la misma prioridad: %(codigos)s.")
                % {"fecha": fecha, "codigos": ", ".join(s.code for s in empatadas)}
            )
        )
    return ganadora


# ---------------------------------------------------------------------------
# Tarifa
# ---------------------------------------------------------------------------


def specificity(rate: Rate) -> int:
    """Cuanto de concreta es una tarifa. Mas puntos, mas especifica.

    Sirve para desempatar dos tarifas con la misma prioridad: entre "todas las
    categorias, todo el ano" y "esta categoria, en agosto, en el aeropuerto",
    gana la segunda, porque alguien la escribio pensando justo en este caso.
    """
    puntos = 0
    if rate.season_id is not None:
        puntos += 4
    if rate.offices.all():
        puntos += 2
    if rate.max_days is not None or rate.min_days > 1:
        puntos += 1
    if len(rate.categories.all()) == 1:
        puntos += 1
    return puntos


def applicable_rates(*, category, office, channel, days: int, fecha, season) -> list[Rate]:
    """Tarifas que encajan con la consulta, sin ordenar todavia."""
    candidatas = (
        Rate.objects.active()
        .filter(categories=category, channel=channel)
        .filter(Q(offices__isnull=True) | Q(offices=office))
        .prefetch_related("categories", "offices", "tiers")
        .select_related("season")
        .distinct()
    )

    encajan = []
    for tarifa in candidatas:
        if not tarifa.covers_date(fecha) or not tarifa.covers_days(days):
            continue
        # Una tarifa de temporada solo vale si es **la** temporada que manda ese
        # dia. Con otra temporada encima (un puente con mas prioridad), la de la
        # temporada general se queda fuera, que es de lo que va el puente.
        if tarifa.season_id is not None and (season is None or tarifa.season_id != season.pk):
            continue
        encajan.append(tarifa)
    return encajan


def resolve_rate(*, category, office, channel, days: int, fecha, season=None) -> Rate:
    """La tarifa que se aplica. Explota antes que elegir a ciegas."""
    candidatas = applicable_rates(
        category=category, office=office, channel=channel, days=days, fecha=fecha, season=season
    )
    if not candidatas:
        raise NoRateAvailable(
            str(
                _(
                    "No hay tarifa para %(categoria)s en %(oficina)s el %(fecha)s "
                    "(%(dias)s dias, canal %(canal)s)."
                )
                % {
                    "categoria": category,
                    "oficina": office,
                    "fecha": fecha,
                    "dias": days,
                    "canal": channel,
                }
            )
        )

    candidatas.sort(key=lambda r: (r.priority, specificity(r)), reverse=True)
    mejor = candidatas[0]
    clave_mejor = (mejor.priority, specificity(mejor))
    empatadas = [r for r in candidatas if (r.priority, specificity(r)) == clave_mejor]
    if len(empatadas) > 1:
        raise AmbiguousRate(
            str(
                _(
                    "Hay %(n)s tarifas igual de aplicables (%(codigos)s). Ajusta la "
                    "prioridad para que solo gane una."
                )
                % {"n": len(empatadas), "codigos": ", ".join(r.code for r in empatadas)}
            )
        )
    return mejor


# ---------------------------------------------------------------------------
# Tramos
# ---------------------------------------------------------------------------


def resolve_tier(rate: Rate, days: int):
    """Tramo en el que cae la duracion."""
    for tramo in sorted(rate.tiers.all(), key=lambda t: t.min_days):
        if tramo.covers(days):
            return tramo
    raise NoTierAvailable(
        str(
            _("La tarifa %(codigo)s no tiene tramo para %(dias)s dias.")
            % {"codigo": rate.code, "dias": days}
        )
    )


def tier_allocation(rate: Rate, days: int) -> list[tuple[object, int, Decimal]]:
    """Reparto de los dias entre tramos: [(tramo, dias, precio_por_dia)].

    En modo **plano** (el estandar del sector) todos los dias van al precio del
    tramo en el que cae la duracion total: 5 dias con el tramo 4-7 a 40 son
    5 x 40. Es lo que produce el escalon conocido de 7 a 8 dias, en el que el
    total **baja** al alargar el alquiler. No es un error: es el incentivo a
    alquilar mas dias, y esta cubierto por un test para que nadie lo "arregle".

    En modo **progresivo** cada dia se paga al precio de su propio tramo, como
    los tramos de una tarifa electrica.
    """
    if rate.tier_mode == TierMode.PROGRESSIVE:
        reparto = []
        for tramo in sorted(rate.tiers.all(), key=lambda t: t.min_days):
            dias_en_tramo = tramo.days_within(days)
            if dias_en_tramo > 0:
                reparto.append((tramo, dias_en_tramo, tramo.price_per_day))
        cubiertos = sum(dias for _tramo, dias, _precio in reparto)
        if cubiertos != days:
            # Tramos con huecos (empiezan en el dia 3, o saltan del 7 al 10):
            # cobrar solo los dias cubiertos seria regalar los demas.
            raise NoTierAvailable(
                str(
                    _(
                        "Los tramos de %(codigo)s no cubren los %(dias)s dias enteros "
                        "(cubren %(cubiertos)s)."
                    )
                    % {"codigo": rate.code, "dias": days, "cubiertos": cubiertos}
                )
            )
        return reparto

    tramo = resolve_tier(rate, days)
    return [(tramo, days, tramo.price_per_day)]


# ---------------------------------------------------------------------------
# Conflictos entre tarifas
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateConflict:
    """Dos tarifas que competirian por la misma venta.

    Es exactamente lo que provocaria un `AmbiguousRate` en el mostrador, pero
    visto antes: la pantalla de conflictos las lista para que alguien ajuste una
    prioridad **hoy**, y no cuando un cliente esta delante esperando el precio.
    """

    rate_a: object
    rate_b: object
    category: object
    channel: str
    #: Dias en los que las dos encajan (para poder reproducirlo).
    sample_days: int

    @property
    def reason(self) -> str:
        return str(
            _(
                "Misma prioridad (%(prioridad)s) y misma especificidad: no hay forma "
                "de saber cual gana."
            )
            % {"prioridad": self.rate_a.priority}
        )


def _rangos_se_solapan(desde_a, hasta_a, desde_b, hasta_b) -> bool:
    """Solape de dos intervalos con extremos abiertos (None = sin limite)."""
    if hasta_a is not None and desde_b is not None and hasta_a < desde_b:
        return False
    return not (hasta_b is not None and desde_a is not None and hasta_b < desde_a)


def _dias_en_comun(rate_a: Rate, rate_b: Rate) -> int | None:
    """Primer numero de dias en el que las dos tarifas encajan, o None."""
    desde = max(rate_a.min_days, rate_b.min_days)
    hasta_a = rate_a.max_days
    hasta_b = rate_b.max_days
    hasta = min(x for x in (hasta_a, hasta_b) if x is not None) if (hasta_a or hasta_b) else desde
    return desde if desde <= hasta else None


def find_rate_conflicts() -> list[RateConflict]:
    """Parejas de tarifas activas que se pisan.

    Compara cada pareja que comparte canal y categoria: si sus oficinas se
    cruzan (o alguna vale para todas), sus vigencias se solapan, sus ventanas de
    dias se cruzan y tienen la misma temporada, entonces las dos son aplicables
    a la vez. Si ademas empatan en prioridad y en especificidad, el motor no
    sabria elegir: eso es el conflicto.
    """
    tarifas = list(
        Rate.objects.active()
        .prefetch_related("categories", "offices", "tiers")
        .select_related("season")
        .order_by("code")
    )

    conflictos: list[RateConflict] = []
    for indice, tarifa_a in enumerate(tarifas):
        for tarifa_b in tarifas[indice + 1 :]:
            if tarifa_a.channel != tarifa_b.channel:
                continue
            if tarifa_a.season_id != tarifa_b.season_id:
                # Temporadas distintas nunca compiten: solo manda una por fecha.
                continue
            if (tarifa_a.priority, specificity(tarifa_a)) != (
                tarifa_b.priority,
                specificity(tarifa_b),
            ):
                continue
            if not _rangos_se_solapan(
                tarifa_a.valid_from, tarifa_a.valid_to, tarifa_b.valid_from, tarifa_b.valid_to
            ):
                continue

            dias = _dias_en_comun(tarifa_a, tarifa_b)
            if dias is None:
                continue

            oficinas_a = {o.pk for o in tarifa_a.offices.all()}
            oficinas_b = {o.pk for o in tarifa_b.offices.all()}
            # Una lista vacia significa "todas": choca con cualquier otra.
            if oficinas_a and oficinas_b and not (oficinas_a & oficinas_b):
                continue

            comunes = {c.pk: c for c in tarifa_a.categories.all()}
            for categoria in tarifa_b.categories.all():
                if categoria.pk in comunes:
                    conflictos.append(
                        RateConflict(
                            rate_a=tarifa_a,
                            rate_b=tarifa_b,
                            category=categoria,
                            channel=tarifa_a.channel,
                            sample_days=dias,
                        )
                    )
    return conflictos
