"""Validacion de los tramos de una tarifa.

Un tramo mal puesto no da un error: da un precio equivocado, y eso no se ve
hasta que alguien firma el contrato. Por eso los tramos de una tarifa tienen
que cubrir la recta de dias **entera y sin pisarse**:

- empiezan en el dia 1;
- cada tramo empieza justo donde acaba el anterior (sin huecos);
- ninguno se solapa con otro;
- solo el ultimo puede quedar abierto (`max_days` vacio), y conviene que lo
  este: sin el, un alquiler de dos meses se queda sin precio.

Es una funcion pura sobre tuplas: la usan el formulario (al guardar), el aviso
en vivo del editor y los tests, sin base de datos de por medio.
"""

from dataclasses import dataclass
from itertools import pairwise

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class TierSpec:
    """Un tramo, tal y como se teclea en el editor."""

    min_days: int
    max_days: int | None
    price_per_day: object = None


def validate_tiers(tiers: list[TierSpec]) -> list[str]:
    """Devuelve la lista de problemas. Vacia significa que los tramos valen.

    No lanza excepcion a proposito: quien llama decide si eso es un error de
    formulario o un aviso que se pinta mientras el usuario escribe.
    """
    problemas: list[str] = []
    if not tiers:
        return [str(_("Una tarifa sin tramos no tiene precio: anade al menos uno."))]

    ordenados = sorted(tiers, key=lambda t: t.min_days)

    for tramo in ordenados:
        if tramo.min_days < 1:
            problemas.append(str(_("Un tramo no puede empezar antes del dia 1.")))
        if tramo.max_days is not None and tramo.max_days < tramo.min_days:
            problemas.append(
                str(
                    _("El tramo que empieza en el dia %(desde)s acaba antes de empezar.")
                    % {"desde": tramo.min_days}
                )
            )

    if ordenados[0].min_days != 1:
        problemas.append(
            str(
                _("Los tramos tienen que empezar en el dia 1, y este empieza en el %(dia)s.")
                % {"dia": ordenados[0].min_days}
            )
        )

    abiertos = [t for t in ordenados if t.max_days is None]
    if len(abiertos) > 1:
        problemas.append(str(_("Solo el ultimo tramo puede quedar abierto (sin dia de fin).")))
    elif abiertos and abiertos[0] is not ordenados[-1]:
        problemas.append(
            str(
                _("El tramo abierto tiene que ser el ultimo, y hay tramos despues del %(dia)s.")
                % {"dia": abiertos[0].min_days}
            )
        )

    for anterior, siguiente in pairwise(ordenados):
        if anterior.max_days is None:
            continue  # ya avisado arriba: un tramo abierto en medio
        if siguiente.min_days <= anterior.max_days:
            problemas.append(
                str(
                    _("Los tramos %(a)s y %(b)s se solapan.")
                    % {"a": _describir(anterior), "b": _describir(siguiente)}
                )
            )
        elif siguiente.min_days > anterior.max_days + 1:
            faltan = _describir_hueco(anterior.max_days + 1, siguiente.min_days - 1)
            problemas.append(
                str(
                    _("Falta el tramo de %(hueco)s: ningun tramo cubre esos dias.")
                    % {"hueco": faltan}
                )
            )

    if ordenados[-1].max_days is not None:
        problemas.append(
            str(
                _(
                    "El ultimo tramo acaba en el dia %(dia)s: un alquiler mas largo se "
                    "quedaria sin precio. Deja vacio el dia de fin."
                )
                % {"dia": ordenados[-1].max_days}
            )
        )

    return problemas


def _describir(tramo: TierSpec) -> str:
    if tramo.max_days is None:
        return f"{tramo.min_days}+"
    return f"{tramo.min_days}-{tramo.max_days}"


def _describir_hueco(desde: int, hasta: int) -> str:
    return f"dia {desde}" if desde == hasta else f"dias {desde} a {hasta}"


def coverage_summary(tiers: list[TierSpec]) -> str:
    """Resumen legible de lo que cubren los tramos: '1-3, 4-7, 8+'."""
    if not tiers:
        return ""
    return ", ".join(_describir(t) for t in sorted(tiers, key=lambda t: t.min_days))
