"""Busqueda y filtros del listado de reservas."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.fleet.models import VehicleCategory
from apps.offices.models import Office

from .models import Reservation, ReservationStatus


class ReservationFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    status = filters.ChoiceFilter(
        label=_("Estado"), choices=ReservationStatus.choices, empty_label=_("Estado: todos")
    )
    category = filters.ModelChoiceFilter(
        label=_("Categoria"),
        queryset=VehicleCategory.objects.active().order_by("sort_order", "name"),
        empty_label=_("Categoria: todas"),
    )
    pickup_office = filters.ModelChoiceFilter(
        label=_("Oficina"),
        queryset=Office.objects.active().order_by("name"),
        empty_label=_("Oficina: todas"),
    )
    pendientes = filters.BooleanFilter(
        label=_("Sin coche asignado"),
        method="filtrar_pendientes",
        widget=filters.widgets.BooleanWidget(),
    )

    class Meta:
        model = Reservation
        fields = ["q", "status", "category", "pickup_office", "pendientes"]

    def buscar(self, queryset, name, value):
        """Por numero de reserva, cliente o matricula."""
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(number__icontains=termino)
            | Q(customer__search_text__contains=termino.lower())
            | Q(vehicle__plate__icontains=termino)
        )

    def filtrar_pendientes(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(vehicle__isnull=value)
