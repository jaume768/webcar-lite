"""Busqueda y filtros de los listados de flota."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin
from apps.offices.models import Office

from .models import (
    BlockReason,
    Fuel,
    MaintenanceKind,
    MaintenanceRecord,
    MaintenanceStatus,
    Transmission,
    Vehicle,
    VehicleBlock,
    VehicleCategory,
    VehicleStatus,
)


class VehicleCategoryFilter(EstadoFilterMixin):
    """El listado ensena tambien las categorias retiradas.

    Es a proposito: son las que aparecen en el historico, y desde aqui se
    vuelven a poner en catalogo. El filtro de estado las separa cuando estorban.
    """

    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    transmission = filters.ChoiceFilter(
        label=_("Cambio"),
        choices=Transmission.choices,
        empty_label=_("Cambio: todos"),
    )
    fuel = filters.ChoiceFilter(
        label=_("Combustible"),
        choices=Fuel.choices,
        empty_label=_("Combustible: todos"),
    )

    class Meta:
        model = VehicleCategory
        fields = ["q", "transmission", "fuel", "estado"]

    def buscar(self, queryset, name, value):
        termino = value.strip()
        if not termino:
            return queryset
        return queryset.filter(Q(name__icontains=termino) | Q(code__icontains=termino))


class VehicleFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    category = filters.ModelChoiceFilter(
        label=_("Categoria"),
        queryset=VehicleCategory.objects.order_by("sort_order", "name"),
        empty_label=_("Categoria: todas"),
    )
    current_office = filters.ModelChoiceFilter(
        label=_("Oficina"),
        queryset=Office.objects.order_by("name"),
        empty_label=_("Oficina: todas"),
    )
    status = filters.ChoiceFilter(
        label=_("Situacion"),
        choices=VehicleStatus.choices,
        empty_label=_("Situacion: todas"),
    )

    class Meta:
        model = Vehicle
        fields = ["q", "category", "current_office", "status", "estado"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El desplegable de oficinas no puede ofrecer oficinas que el usuario no
        # tiene: seria ensenarle que existen y ademas no devolveria nada.
        usuario = getattr(self.request, "user", None)
        if usuario is not None:
            from apps.offices.selectors import offices_for_user

            self.filters["current_office"].queryset = offices_for_user(usuario).order_by("name")

    def buscar(self, queryset, name, value):
        termino = value.strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(plate__icontains=termino)
            | Q(brand__icontains=termino)
            | Q(model__icontains=termino)
            | Q(vin__icontains=termino)
        )


class VehicleBlockFilter(filters.FilterSet):
    """Los bloqueos no se desactivan: se anulan. Aqui no hay filtro de estado."""

    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    reason = filters.ChoiceFilter(
        label=_("Motivo"),
        choices=BlockReason.choices,
        empty_label=_("Motivo: todos"),
    )
    vigencia = filters.ChoiceFilter(
        label=_("Vigencia"),
        choices=[
            ("vigentes", _("En curso y futuros")),
            ("pasados", _("Terminados")),
        ],
        method="filtrar_vigencia",
        empty_label=_("Vigencia: todas"),
    )

    class Meta:
        model = VehicleBlock
        fields = ["q", "reason", "vigencia"]

    def filtrar_vigencia(self, queryset, name, value):
        from django.utils import timezone

        ahora = timezone.now()
        if value == "vigentes":
            return queryset.filter(end_at__gt=ahora)
        return queryset.filter(end_at__lte=ahora)

    def buscar(self, queryset, name, value):
        termino = value.strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(vehicle__plate__icontains=termino)
            | Q(vehicle__brand__icontains=termino)
            | Q(vehicle__model__icontains=termino)
            | Q(notes__icontains=termino)
        )


class MaintenanceFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    status = filters.ChoiceFilter(
        label=_("Estado"), choices=MaintenanceStatus.choices, empty_label=_("Estado: todos")
    )
    kind = filters.ChoiceFilter(
        label=_("Tipo"), choices=MaintenanceKind.choices, empty_label=_("Tipo: todos")
    )

    class Meta:
        model = MaintenanceRecord
        fields = ["q", "status", "kind"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(vehicle__plate__icontains=termino)
            | Q(workshop__icontains=termino)
            | Q(description__icontains=termino)
        )
