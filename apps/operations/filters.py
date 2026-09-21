"""Filtros del listado de multas."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import FineStatus, TrafficFine


class TrafficFineFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    status = filters.ChoiceFilter(
        label=_("Estado"), choices=FineStatus.choices, empty_label=_("Estado: todos")
    )

    class Meta:
        model = TrafficFine
        fields = ["q", "status"]

    def buscar(self, queryset, name, value):
        """Por expediente, matricula, reserva o conductor."""
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(file_number__icontains=termino)
            | Q(vehicle__plate__icontains=termino)
            | Q(reservation__number__icontains=termino)
            | Q(driver_name__icontains=termino)
            | Q(authority__icontains=termino)
        )
