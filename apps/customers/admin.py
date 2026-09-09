from django.contrib import admin

from .models import Customer, CustomerDocument


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "last_name",
        "first_name",
        "document_number",
        "phone",
        "is_blacklisted",
        "is_active",
    ]
    list_filter = ["is_active", "is_blacklisted", "document_type", "office"]
    search_fields = ["first_name", "last_name", "document_number", "email", "phone"]
    readonly_fields = ["search_text"]

    def has_delete_permission(self, request, obj=None):
        # Un cliente borrado deja reservas y facturas sin titular.
        return False


@admin.register(CustomerDocument)
class CustomerDocumentAdmin(admin.ModelAdmin):
    """Los ficheros son privados: desde aqui solo se ve que existen."""

    list_display = ["customer", "kind", "original_name", "created_at"]
    list_filter = ["kind"]
    search_fields = ["customer__last_name", "customer__document_number"]
