from django.contrib import admin

from .models import Office, OfficePool


class NoBorrarMixin:
    """Los maestros se desactivan: las reservas historicas apuntan a ellos."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Office)
class OfficeAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["name", "code", "city", "pool", "phone", "is_active"]
    list_filter = ["is_active", "province", "pool"]
    search_fields = ["name", "code", "city"]


@admin.register(OfficePool)
class OfficePoolAdmin(NoBorrarMixin, admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]
