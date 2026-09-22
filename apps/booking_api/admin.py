"""Admin de soporte. Las claves se crean y renuevan con `manage.py api_client`."""

from django.contrib import admin

from .models import ApiClient, ApiReservation


@admin.register(ApiClient)
class ApiClientAdmin(admin.ModelAdmin):
    list_display = ["name", "key_prefix", "is_active", "last_used_at"]
    list_filter = ["is_active"]
    fields = ["name", "user", "key_prefix", "is_active", "last_used_at"]
    readonly_fields = ["user", "key_prefix", "last_used_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiReservation)
class ApiReservationAdmin(admin.ModelAdmin):
    list_display = ["reservation", "api_client", "external_ref", "created_at"]
    list_filter = ["api_client"]
    search_fields = ["reservation__number", "external_ref", "idempotency_key"]
    readonly_fields = [
        "api_client",
        "reservation",
        "idempotency_key",
        "request_hash",
        "external_ref",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
