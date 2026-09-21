"""Busqueda y filtros del listado de politicas."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin

from .models import Policy


class PolicyFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    show_on_invoice = filters.BooleanFilter(
        label=_("En facturas"),
        widget=filters.widgets.BooleanWidget(),
    )

    class Meta:
        model = Policy
        fields = ["q", "show_on_invoice", "estado"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(Q(title__icontains=termino) | Q(body__icontains=termino))
