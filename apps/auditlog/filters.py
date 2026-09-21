"""Filtros del listado de auditoria."""

import django_filters as filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import AuditAction, AuditLog


def _fecha(etiqueta):
    return forms.DateInput(attrs={"type": "date", "title": etiqueta, "aria-label": etiqueta})


class AuditLogFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    action = filters.ChoiceFilter(
        label=_("Acción"), choices=AuditAction.choices, empty_label=_("Acción: todas")
    )
    desde = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__gte",
        label=_("Desde"),
        widget=_fecha(_("Desde")),
    )
    hasta = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__lte",
        label=_("Hasta"),
        widget=_fecha(_("Hasta")),
    )

    class Meta:
        model = AuditLog
        fields = ["q", "action", "desde", "hasta"]

    def buscar(self, queryset, name, value):
        """Por texto del apunte, objeto, usuario o IP."""
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(message__icontains=termino)
            | Q(object_repr__icontains=termino)
            | Q(actor_repr__icontains=termino)
            | Q(ip__startswith=termino)
        )
