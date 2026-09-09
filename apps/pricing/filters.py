"""Busqueda y filtros de los listados de tarifas."""

import django_filters as filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin

from .models import (
    CalculationType,
    Channel,
    Discount,
    Extra,
    Rate,
    Season,
    Supplement,
    SupplementType,
    TierMode,
)


def _buscar_por_codigo_y_nombre(queryset, value):
    termino = (value or "").strip()
    if not termino:
        return queryset
    return queryset.filter(Q(name__icontains=termino) | Q(code__icontains=termino))


class ExtraFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    calculation_type = filters.ChoiceFilter(
        label=_("Forma de cobro"),
        choices=CalculationType.choices,
        empty_label=_("Cobro: todos"),
    )

    class Meta:
        model = Extra
        fields = ["q", "calculation_type", "estado"]

    def buscar(self, queryset, name, value):
        return _buscar_por_codigo_y_nombre(queryset, value)


class SeasonFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    vigencia = filters.ChoiceFilter(
        label=_("Vigencia"),
        choices=[("vigentes", _("En curso y futuras")), ("pasadas", _("Terminadas"))],
        method="filtrar_vigencia",
        empty_label=_("Vigencia: todas"),
    )

    class Meta:
        model = Season
        fields = ["q", "vigencia", "estado"]

    def buscar(self, queryset, name, value):
        return _buscar_por_codigo_y_nombre(queryset, value)

    def filtrar_vigencia(self, queryset, name, value):
        from django.utils import timezone

        hoy = timezone.localdate()
        if value == "vigentes":
            return queryset.filter(end_date__gte=hoy)
        return queryset.filter(end_date__lt=hoy)


class RateFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    channel = filters.ChoiceFilter(
        label=_("Canal"), choices=Channel.choices, empty_label=_("Canal: todos")
    )
    season = filters.ModelChoiceFilter(
        label=_("Temporada"),
        queryset=Season.objects.order_by("-priority", "name"),
        empty_label=_("Temporada: todas"),
    )
    tier_mode = filters.ChoiceFilter(
        label=_("Tramos"), choices=TierMode.choices, empty_label=_("Tramos: todos")
    )

    class Meta:
        model = Rate
        fields = ["q", "channel", "season", "tier_mode", "estado"]

    def buscar(self, queryset, name, value):
        return _buscar_por_codigo_y_nombre(queryset, value)


class SupplementFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    supplement_type = filters.ChoiceFilter(
        label=_("Tipo"), choices=SupplementType.choices, empty_label=_("Tipo: todos")
    )

    class Meta:
        model = Supplement
        fields = ["q", "supplement_type", "estado"]

    def buscar(self, queryset, name, value):
        return _buscar_por_codigo_y_nombre(queryset, value)


class DiscountFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    con_codigo = filters.ChoiceFilter(
        label=_("Codigo"),
        choices=[("si", _("Con codigo")), ("no", _("Automaticos"))],
        method="filtrar_codigo",
        empty_label=_("Codigo: todos"),
    )

    class Meta:
        model = Discount
        fields = ["q", "con_codigo", "estado"]

    def buscar(self, queryset, name, value):
        return _buscar_por_codigo_y_nombre(queryset, value)

    def filtrar_codigo(self, queryset, name, value):
        return queryset.exclude(code="") if value == "si" else queryset.filter(code="")
