"""Servicios de tarifas.

`pricing.services.rental_days()` es el unico sitio del sistema donde se calcula
la duracion de un alquiler, y `calculate_reservation_price()` el unico donde se
calcula un precio. Todo lo que necesite un importe pasa por aqui.
"""

from .catalog import (
    PricingServiceError,
    duplicate_rate,
    save_discount,
    save_extra,
    save_rate,
    save_season,
    save_supplement,
    set_discount_active,
    set_extra_active,
    set_rate_active,
    set_season_active,
    set_supplement_active,
)
from .duration import InvalidRentalPeriod, courtesy_margin, rental_days
from .engine import calculate_reservation_price, redondear
from .rates import (
    AmbiguousRate,
    AmbiguousSeason,
    NoRateAvailable,
    NoTierAvailable,
    PricingError,
    RateConflict,
    applicable_rates,
    find_rate_conflicts,
    resolve_rate,
    resolve_season,
    resolve_tier,
    specificity,
    tier_allocation,
)
from .tiers import TierSpec, coverage_summary, validate_tiers

__all__ = [
    "AmbiguousRate",
    "AmbiguousSeason",
    "InvalidRentalPeriod",
    "NoRateAvailable",
    "NoTierAvailable",
    "PricingError",
    "PricingServiceError",
    "RateConflict",
    "TierSpec",
    "applicable_rates",
    "calculate_reservation_price",
    "courtesy_margin",
    "coverage_summary",
    "duplicate_rate",
    "find_rate_conflicts",
    "redondear",
    "rental_days",
    "resolve_rate",
    "resolve_season",
    "resolve_tier",
    "save_discount",
    "save_extra",
    "save_rate",
    "save_season",
    "save_supplement",
    "set_discount_active",
    "set_extra_active",
    "set_rate_active",
    "set_season_active",
    "set_supplement_active",
    "specificity",
    "tier_allocation",
    "validate_tiers",
]
