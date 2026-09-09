"""Admin interno de tarifas.

Es herramienta de soporte tecnico, no el panel de gestion: las pantallas de
mantenimiento de tarifas llegan con su propio prompt. Mientras tanto, desde
aqui se pueden montar y revisar los datos que consume el motor.
"""

from django.contrib import admin

from .models import Discount, Extra, Rate, RateTier, Season, Supplement


class NoBorrarMixin:
    def has_delete_permission(self, request, obj=None):
        # Las reservas historicas apuntan a la tarifa con la que se vendieron.
        return False


@admin.register(Extra)
class ExtraAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["sort_order", "code", "name", "calculation_type", "price", "is_active"]
    list_filter = ["is_active", "calculation_type"]
    search_fields = ["code", "name"]
    ordering = ["sort_order", "name"]


@admin.register(Season)
class SeasonAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["name", "code", "start_date", "end_date", "priority", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]


class RateTierInline(admin.TabularInline):
    """Los tramos se editan dentro de su tarifa: sueltos no significan nada."""

    model = RateTier
    extra = 1
    ordering = ["min_days"]


@admin.register(Rate)
class RateAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["code", "name", "channel", "season", "tier_mode", "priority", "is_active"]
    list_filter = ["is_active", "channel", "tier_mode", "season"]
    search_fields = ["code", "name"]
    filter_horizontal = ["categories", "offices"]
    inlines = [RateTierInline]


@admin.register(Supplement)
class SupplementAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["code", "name", "supplement_type", "amount_type", "amount", "is_active"]
    list_filter = ["is_active", "supplement_type", "amount_type"]
    search_fields = ["code", "name"]
    filter_horizontal = ["offices"]


@admin.register(Discount)
class DiscountAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["name", "code", "amount_type", "amount", "valid_from", "valid_to", "is_active"]
    list_filter = ["is_active", "amount_type"]
    search_fields = ["code", "name"]
    filter_horizontal = ["categories"]
