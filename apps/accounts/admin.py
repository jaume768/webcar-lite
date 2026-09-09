"""Admin de Django: herramienta de soporte tecnico, no panel de gestion.

El cliente gestiona usuarios en /usuarios/. Esto queda para incidencias, tras
/admin-interno/ y solo para staff.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Role, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "role", "is_active", "is_staff"]
    list_filter = ["is_active", "is_staff", "is_superuser", "role", "offices"]
    search_fields = ["email", "first_name", "last_name"]
    filter_horizontal = ["offices", "groups", "user_permissions"]

    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Datos personales", {"fields": ["first_name", "last_name", "phone"]}),
        ("Acceso", {"fields": ["role", "offices", "is_active", "is_staff", "is_superuser"]}),
        ("Permisos individuales", {"fields": ["groups", "user_permissions"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "password1", "password2"]}),
    ]

    def has_delete_permission(self, request, obj=None):
        # Un usuario nunca se borra: se desactiva. Borrarlo rompe la auditoria.
        return False


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_system"]
    search_fields = ["name", "code"]
    filter_horizontal = ["permissions"]
