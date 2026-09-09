from django.contrib import admin

from .models import Vehicle, VehicleBlock, VehicleCategory


class NoBorrarMixin:
    """Maestros de flota: se dan de baja, no se borran."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VehicleCategory)
class VehicleCategoryAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["sort_order", "code", "name", "transmission", "fuel", "is_active"]
    list_filter = ["is_active", "transmission", "fuel"]
    search_fields = ["code", "name"]
    ordering = ["sort_order", "name"]


@admin.register(Vehicle)
class VehicleAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["plate", "brand", "model", "category", "current_office", "status", "is_active"]
    list_filter = ["is_active", "status", "current_office", "category"]
    search_fields = ["plate", "brand", "model", "vin"]
    autocomplete_fields = ["category", "current_office"]


@admin.register(VehicleBlock)
class VehicleBlockAdmin(admin.ModelAdmin):
    # El bloqueo si se borra: es agenda, no historico (ver services.delete_block).
    list_display = ["vehicle", "start_at", "end_at", "reason"]
    list_filter = ["reason"]
    search_fields = ["vehicle__plate"]
    autocomplete_fields = ["vehicle"]
