"""Busqueda y filtros de los listados de oficinas y grupos.

django-filter es quien define que se puede filtrar: la vista no vuelve a tocar
el queryset. El campo `q` es el buscador de la cabecera de la tabla; el resto
se pintan como desplegables.
"""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin

from .models import Office, OfficePool


class OfficeFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    pool = filters.ModelChoiceFilter(
        label=_("Grupo"),
        queryset=OfficePool.objects.order_by("name"),
        empty_label=_("Grupo: todos"),
    )
    province = filters.ChoiceFilter(
        label=_("Provincia"),
        choices=[],
        empty_label=_("Provincia: todas"),
    )

    class Meta:
        model = Office
        fields = ["q", "pool", "province", "estado"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Las provincias salen de los datos: no hay catalogo que mantener y el
        # desplegable no ofrece nunca una opcion sin resultados.
        provincias = (
            Office.objects.exclude(province="")
            .order_by("province")
            .values_list("province", flat=True)
            .distinct()
        )
        self.filters["province"].extra["choices"] = [(p, p) for p in provincias]

    def buscar(self, queryset, name, value):
        termino = value.strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(name__icontains=termino)
            | Q(code__icontains=termino)
            | Q(city__icontains=termino)
            | Q(province__icontains=termino)
        )


class OfficePoolFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))

    class Meta:
        model = OfficePool
        fields = ["q", "estado"]

    def buscar(self, queryset, name, value):
        termino = value.strip()
        if not termino:
            return queryset
        return queryset.filter(Q(name__icontains=termino) | Q(code__icontains=termino))
