"""Consulta de la auditoria. Solo lectura: aqui no se escribe ni se borra nada."""

from django.utils.translation import gettext_lazy as _

from apps.core.crud import CrudListView
from apps.core.tables import Column

from .filters import AuditLogFilter
from .models import AuditLog


class AuditLogListView(CrudListView):
    permission_required = "auditlog.view_auditlog"
    model = AuditLog
    scope_to_user = True
    filterset_class = AuditLogFilter
    table_id = "tabla-auditoria"
    table_row_template = "auditlog/_entry_row.html"
    table_columns = [
        Column(label=_("Cuándo"), css="w-36"),
        Column(label=_("Usuario")),
        Column(label=_("Acción")),
        Column(label=_("Qué pasó")),
        Column(label=_("Origen")),
    ]
    search_placeholder = _("Texto, objeto, usuario o IP...")
    empty_title = _("Sin apuntes")
    empty_message = _("Cambia la búsqueda o quita algún filtro.")
    page_title = _("Auditoría")
    paginate_by = 50

    def get_base_queryset(self):
        return super().get_base_queryset().select_related("actor", "content_type")
