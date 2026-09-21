"""Busqueda y filtros de los listados de facturacion.

django-filter define que se puede filtrar; la vista no vuelve a tocar el
queryset. El scope de oficina lo pone la vista antes, en el queryset base.
"""

import django_filters as filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin
from apps.offices.models import Office
from apps.reservations.models import Reservation

from .models import (
    Invoice,
    InvoiceKind,
    InvoiceSeries,
    Payment,
    PaymentMethod,
    PaymentType,
)


def _fecha(etiqueta):
    return forms.DateInput(attrs={"type": "date", "title": etiqueta, "aria-label": etiqueta})


class InvoiceFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    kind = filters.ChoiceFilter(
        label=_("Tipo"), choices=InvoiceKind.choices, empty_label=_("Tipo: todos")
    )
    series = filters.ModelChoiceFilter(
        label=_("Serie"),
        queryset=InvoiceSeries.objects.order_by("kind", "code"),
        empty_label=_("Serie: todas"),
    )
    desde = filters.DateFilter(
        field_name="issued_at", lookup_expr="date__gte", label=_("Desde"), widget=_fecha(_("Desde"))
    )
    hasta = filters.DateFilter(
        field_name="issued_at", lookup_expr="date__lte", label=_("Hasta"), widget=_fecha(_("Hasta"))
    )

    class Meta:
        model = Invoice
        fields = ["q", "kind", "series", "desde", "hasta"]

    def buscar(self, queryset, name, value):
        """Por numero de factura o de reserva, nombre o NIF del cliente."""
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(number__icontains=termino)
            | Q(reservation__number__icontains=termino)
            | Q(customer_name__icontains=termino)
            | Q(customer_tax_id__icontains=termino)
        )


class PaymentFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    method = filters.ChoiceFilter(
        label=_("Medio"), choices=PaymentMethod.choices, empty_label=_("Medio: todos")
    )
    payment_type = filters.ChoiceFilter(
        label=_("Concepto"), choices=PaymentType.choices, empty_label=_("Concepto: todos")
    )
    office = filters.ModelChoiceFilter(
        label=_("Oficina"),
        queryset=Office.objects.active().order_by("name"),
        empty_label=_("Oficina: todas"),
    )
    desde = filters.DateFilter(
        field_name="paid_at", lookup_expr="date__gte", label=_("Desde"), widget=_fecha(_("Desde"))
    )
    hasta = filters.DateFilter(
        field_name="paid_at", lookup_expr="date__lte", label=_("Hasta"), widget=_fecha(_("Hasta"))
    )

    class Meta:
        model = Payment
        fields = ["q", "method", "payment_type", "office", "desde", "hasta"]

    def buscar(self, queryset, name, value):
        """Por numero de reserva, referencia del cobro o cliente."""
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(reservation__number__icontains=termino)
            | Q(reference__icontains=termino)
            | Q(reservation__customer__search_text__contains=termino.lower())
        )


class ToInvoiceFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    pickup_office = filters.ModelChoiceFilter(
        label=_("Oficina"),
        queryset=Office.objects.active().order_by("name"),
        empty_label=_("Oficina: todas"),
    )

    class Meta:
        model = Reservation
        fields = ["q", "pickup_office"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(number__icontains=termino)
            | Q(customer__search_text__contains=termino.lower())
            | Q(vehicle__plate__icontains=termino)
        )


class InvoiceSeriesFilter(EstadoFilterMixin):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    kind = filters.ChoiceFilter(
        label=_("Tipo"), choices=InvoiceKind.choices, empty_label=_("Tipo: todos")
    )

    class Meta:
        model = InvoiceSeries
        fields = ["q", "kind", "estado"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(Q(code__icontains=termino) | Q(name__icontains=termino))
