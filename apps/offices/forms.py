"""Formularios de oficinas y grupos.

Validan y normalizan; guardar es cosa del servicio. `is_active` no se toca
desde aqui: el alta y la baja tienen su propia accion, para que desactivar una
oficina sea una decision explicita y no un descuido al editar la direccion.
"""

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import Office, OfficePool


class OfficeForm(forms.ModelForm):
    class Meta:
        model = Office
        fields = [
            "code",
            "name",
            "pool",
            "address",
            "city",
            "province",
            "postal_code",
            "country",
            "phone",
            "email",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Un grupo desactivado no se ofrece, pero si la oficina ya lo tiene se
        # conserva: editar el telefono no puede cambiarle el grupo por detras.
        grupos = OfficePool.objects.active()
        if self.instance.pk and self.instance.pool_id:
            grupos = OfficePool.objects.filter(Q(is_active=True) | Q(pk=self.instance.pool_id))
        self.fields["pool"].queryset = grupos.order_by("name")
        self.fields["pool"].empty_label = _("Sin grupo")

    def clean_code(self):
        """El codigo es un identificador, no un texto: siempre en minusculas.

        Sin normalizar, "PMI" y "pmi" serian dos oficinas distintas para la base
        de datos y la misma para quien la teclea.
        """
        return self.cleaned_data["code"].strip().lower()


class OfficePoolForm(forms.ModelForm):
    class Meta:
        model = OfficePool
        fields = ["code", "name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()
