"""Altas y bajas del catalogo: extras, temporadas, tarifas, suplementos y descuentos."""

import structlog
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from ..models import Extra, Rate, RateTier
from .tiers import TierSpec, validate_tiers

logger = structlog.get_logger(__name__)


class PricingServiceError(ServiceError):
    """Regla de negocio incumplida. La vista la convierte en aviso, no en 500."""


@transaction.atomic
def save_extra(*, extra: Extra, actor=None) -> Extra:
    """Crea o actualiza un extra ya validado por su formulario."""
    creando = extra.pk is None
    extra.code = (extra.code or "").strip().lower()
    extra.save()
    logger.info(
        "extra_creado" if creando else "extra_actualizado",
        extra_id=extra.pk,
        code=extra.code,
        actor_id=getattr(actor, "pk", None),
    )
    return extra


@transaction.atomic
def set_extra_active(*, extra: Extra, active: bool, actor=None) -> Extra:
    """Retira un extra del catalogo o lo repone. Nunca borra.

    Las reservas que ya lo llevan siguen igual: el importe cobrado esta en la
    linea de la reserva, no se recalcula desde aqui.
    """
    if active:
        extra.activate()
    else:
        extra.deactivate()
    logger.info(
        "extra_activado" if active else "extra_desactivado",
        extra_id=extra.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return extra


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


@transaction.atomic
def save_season(*, season, actor=None):
    """Crea o actualiza una temporada ya validada por su formulario."""
    creando = season.pk is None
    season.code = (season.code or "").strip().lower()
    season.save()
    logger.info(
        "temporada_creada" if creando else "temporada_actualizada",
        season_id=season.pk,
        code=season.code,
        actor_id=getattr(actor, "pk", None),
    )
    return season


@transaction.atomic
def set_season_active(*, season, active: bool, actor=None):
    """Retira una temporada o la repone. Nunca borra: hay tarifas colgando."""
    if not active and season.rates.filter(is_active=True).exists():
        raise PricingServiceError(
            _("No se puede desactivar %(temporada)s: tiene tarifas activas dentro.")
            % {"temporada": season.name}
        )
    if active:
        season.activate()
    else:
        season.deactivate()
    logger.info(
        "temporada_activada" if active else "temporada_desactivada",
        season_id=season.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return season


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------


@transaction.atomic
def save_rate(*, rate, formset=None, actor=None):
    """Guarda la tarifa **y sus tramos**, o no guarda nada.

    Van en la misma transaccion a proposito: una tarifa sin tramos no tiene
    precio, asi que dejarla a medias es peor que no haberla guardado. Los
    tramos se revalidan aqui, despues de aplicar altas y bajas del formset,
    porque es el unico momento en el que se sabe como queda la lista entera.
    """
    creando = rate.pk is None
    rate.code = (rate.code or "").strip().lower()
    rate.save()

    if formset is not None:
        formset.instance = rate
        formset.save()

        restantes = [
            TierSpec(min_days=t.min_days, max_days=t.max_days, price_per_day=t.price_per_day)
            for t in rate.tiers.all()
        ]
        problemas = validate_tiers(restantes)
        if problemas:
            raise PricingServiceError(" ".join(problemas))

    logger.info(
        "tarifa_creada" if creando else "tarifa_actualizada",
        rate_id=rate.pk,
        code=rate.code,
        actor_id=getattr(actor, "pk", None),
    )
    return rate


@transaction.atomic
def set_rate_active(*, rate, active: bool, actor=None):
    """Retira una tarifa del catalogo o la repone. Nunca borra."""
    if active:
        rate.activate()
    else:
        rate.deactivate()
    logger.info(
        "tarifa_activada" if active else "tarifa_desactivada",
        rate_id=rate.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return rate


@transaction.atomic
def duplicate_rate(*, rate, actor=None):
    """Copia una tarifa con sus tramos, sus categorias y sus oficinas.

    Es como se monta la temporada alta: se duplica la baja y se cambian los
    precios. La copia nace **desactivada** para que nadie venda con ella antes
    de haberla repasado.
    """
    original_pk = rate.pk
    copia = Rate.objects.get(pk=original_pk)
    copia.pk = None
    copia._state.adding = True
    copia.code = _codigo_libre(rate.code)
    copia.name = _("%(nombre)s (copia)") % {"nombre": rate.name}
    copia.is_active = False
    copia.save()

    copia.categories.set(rate.categories.all())
    copia.offices.set(rate.offices.all())
    for tramo in rate.tiers.all():
        RateTier.objects.create(
            rate=copia,
            min_days=tramo.min_days,
            max_days=tramo.max_days,
            price_per_day=tramo.price_per_day,
        )

    logger.info(
        "tarifa_duplicada",
        rate_id=copia.pk,
        origen_id=original_pk,
        code=copia.code,
        actor_id=getattr(actor, "pk", None),
    )
    return copia


def _codigo_libre(code: str) -> str:
    """`eco-baja` -> `eco-baja-copia`, `-copia-2`, `-copia-3`..."""
    base = f"{code}-copia"[:30]
    if not Rate.objects.filter(code=base).exists():
        return base
    for numero in range(2, 100):
        candidato = f"{base}-{numero}"[:30]
        if not Rate.objects.filter(code=candidato).exists():
            return candidato
    raise PricingServiceError(_("Demasiadas copias de esa tarifa. Renombra alguna."))


# ---------------------------------------------------------------------------
# Suplementos y descuentos
# ---------------------------------------------------------------------------


@transaction.atomic
def save_supplement(*, supplement, actor=None):
    creando = supplement.pk is None
    supplement.code = (supplement.code or "").strip().lower()
    supplement.save()
    logger.info(
        "suplemento_creado" if creando else "suplemento_actualizado",
        supplement_id=supplement.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return supplement


@transaction.atomic
def set_supplement_active(*, supplement, active: bool, actor=None):
    if active:
        supplement.activate()
    else:
        supplement.deactivate()
    logger.info(
        "suplemento_activado" if active else "suplemento_desactivado",
        supplement_id=supplement.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return supplement


@transaction.atomic
def save_discount(*, discount, actor=None):
    creando = discount.pk is None
    discount.code = (discount.code or "").strip().lower()
    discount.save()
    logger.info(
        "descuento_creado" if creando else "descuento_actualizado",
        discount_id=discount.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return discount


@transaction.atomic
def set_discount_active(*, discount, active: bool, actor=None):
    if active:
        discount.activate()
    else:
        discount.deactivate()
    logger.info(
        "descuento_activado" if active else "descuento_desactivado",
        discount_id=discount.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return discount
