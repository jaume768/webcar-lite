"""Busqueda y filtros del listado de clientes."""

import django_filters as filters
from django.utils.translation import gettext_lazy as _

from apps.core.filters import EstadoFilterMixin
from apps.offices.models import Office

from .models import Customer, DocumentType


class CustomerFilter(EstadoFilterMixin):
    """La busqueda pasa por `Customer.objects.search()`, que usa el indice trigram."""

    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    document_type = filters.ChoiceFilter(
        label=_("Documento"),
        choices=DocumentType.choices,
        empty_label=_("Documento: todos"),
    )
    office = filters.ModelChoiceFilter(
        label=_("Oficina de alta"),
        queryset=Office.objects.order_by("name"),
        empty_label=_("Oficina: todas"),
    )
    marcados = filters.ChoiceFilter(
        label=_("Marca"),
        choices=[("si", _("Solo conflictivos")), ("no", _("Sin marca"))],
        method="filtrar_marcados",
        empty_label=_("Marca: todos"),
    )

    class Meta:
        model = Customer
        fields = ["q", "document_type", "office", "marcados", "estado"]

    def buscar(self, queryset, name, value):
        return queryset.search(value)

    def filtrar_marcados(self, queryset, name, value):
        return queryset.filter(is_blacklisted=value == "si")
