"""Piezas de filtrado comunes a los listados de maestros."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import Lead, LeadStatus

#: Un maestro no se borra: se desactiva. El listado tiene que poder ver ambos,
#: y por eso, sin filtro, salen todos.
ESTADOS = [("activo", _("Activos")), ("inactivo", _("Desactivados"))]


class EstadoFilterMixin(filters.FilterSet):
    """Filtro de activo/desactivado para cualquier `ActivableModel`."""

    estado = filters.ChoiceFilter(
        label=_("Estado"),
        choices=ESTADOS,
        method="filtrar_estado",
        empty_label=_("Estado: todos"),
    )

    def filtrar_estado(self, queryset, name, value):
        return queryset.filter(is_active=value == "activo")


class LeadFilter(filters.FilterSet):
    """Contactos de la web: se busca por quien escribio y se filtra por estado."""

    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    status = filters.ChoiceFilter(
        label=_("Estado"),
        choices=LeadStatus.choices,
        empty_label=_("Estado: todos"),
    )

    class Meta:
        model = Lead
        fields = ["q", "status"]

    def buscar(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(company__icontains=value)
            | Q(phone__icontains=value)
            | Q(email__icontains=value)
        )
