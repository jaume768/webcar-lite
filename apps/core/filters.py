"""Piezas de filtrado comunes a los listados de maestros."""

import django_filters as filters
from django.utils.translation import gettext_lazy as _

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
