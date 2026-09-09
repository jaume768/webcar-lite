from django.contrib import admin

from .models import Office


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "city", "phone", "is_active"]
    list_filter = ["is_active", "province"]
    search_fields = ["name", "code", "city"]

    def has_delete_permission(self, request, obj=None):
        # Las oficinas se desactivan: las reservas historicas apuntan a ellas.
        return False
