"""Los permisos del rol, sumados a los del propio usuario."""

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import Permission


class RolePermissionsBackend(BaseBackend):
    """Aporta los permisos del rol. No autentica a nadie.

    Django consulta todos los backends y hace la union, asi que este convive
    con ModelBackend (permisos individuales y de grupo) sin duplicar nada.
    """

    def authenticate(self, request, **kwargs):
        return None

    def get_all_permissions(self, user_obj, obj=None):
        if obj is not None or not user_obj.is_active or user_obj.is_anonymous:
            return set()
        if user_obj.is_superuser:
            # PermissionsMixin ya corta antes por is_superuser; esto solo evita
            # una consulta inutil si alguien llama al backend directamente.
            return set()
        if not hasattr(user_obj, "_role_perm_cache"):
            role_id = getattr(user_obj, "role_id", None)
            if role_id is None:
                user_obj._role_perm_cache = set()
            else:
                user_obj._role_perm_cache = {
                    f"{app_label}.{codename}"
                    for app_label, codename in Permission.objects.filter(
                        roles__id=role_id
                    ).values_list("content_type__app_label", "codename")
                }
        return user_obj._role_perm_cache

    def has_perm(self, user_obj, perm, obj=None):
        return perm in self.get_all_permissions(user_obj, obj=obj)
