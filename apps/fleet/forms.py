"""Formularios de flota."""

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateField, DateInput, DateTimeField
from apps.offices.models import Office
from apps.offices.selectors import offices_for_user

from .models import (
    MANUAL_STATUSES,
    MaintenanceRecord,
    Vehicle,
    VehicleBlock,
    VehicleCategory,
    VehicleStatus,
)
from .selectors import selectable_categories


class VehicleCategoryForm(forms.ModelForm):
    class Meta:
        model = VehicleCategory
        fields = [
            "code",
            "name",
            "description",
            "image",
            "seats",
            "doors",
            "luggage",
            "transmission",
            "fuel",
            "air_conditioning",
            "sort_order",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_code(self):
        """El codigo es identificador: siempre en minusculas y sin espacios."""
        return self.cleaned_data["code"].strip().lower()

    def clean_seats(self):
        plazas = self.cleaned_data["seats"]
        if plazas < 1:
            raise forms.ValidationError(_("Una categoria tiene al menos una plaza."))
        return plazas


class VehicleCategoryChoiceField(forms.ModelChoiceField):
    """Selector de categoria para pantallas de venta (reservas, presupuestos).

    Solo ofrece categorias activas. Es el campo que usaran los formularios de
    reserva: asi la regla "una categoria retirada no se vende" se cumple sola,
    tambien si el POST viene manipulado, porque el queryset del campo es el que
    valida.
    """

    def __init__(self, *, queryset=None, **kwargs):
        kwargs.setdefault("label", _("Categoria"))
        kwargs.setdefault("empty_label", _("Elige categoria"))
        super().__init__(queryset=queryset or selectable_categories(), **kwargs)


class VehicleForm(forms.ModelForm):
    """Ficha del vehiculo.

    El estado no esta: se cambia desde su propia accion, que pasa por
    `services.set_vehicle_status` y sabe que cambios no mienten. Editar el color
    de un coche no puede colar de rebote un "disponible".
    """

    registration_date = forms.DateField(
        label=_("primera matriculacion"), required=False, widget=DateInput
    )
    itv_expiry = forms.DateField(label=_("caducidad de la ITV"), required=False, widget=DateInput)
    insurance_expiry = forms.DateField(
        label=_("caducidad del seguro"), required=False, widget=DateInput
    )
    purchase_date = forms.DateField(label=_("fecha de compra"), required=False, widget=DateInput)

    class Meta:
        model = Vehicle
        fields = [
            "plate",
            "brand",
            "model",
            "version",
            "category",
            "current_office",
            "vin",
            "mileage",
            "fuel",
            "transmission",
            "seats",
            "color",
            "registration_date",
            "itv_expiry",
            "insurance_expiry",
            "insurance_company",
            "insurance_policy",
            "purchase_date",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Solo se dan de alta coches en oficinas propias: si no, un usuario
        # podria colocar flota en una oficina que ni siquiera puede ver.
        if user is not None:
            oficinas = offices_for_user(user)
            if self.instance.pk and self.instance.current_office_id:
                # Un coche aparcado en una oficina que ya no es tuya conserva la
                # suya: editar el color no puede moverlo de sitio sin querer.
                oficinas = oficinas | Office.objects.filter(pk=self.instance.current_office_id)
            self.fields["current_office"].queryset = oficinas.distinct().order_by("name")

        categorias = selectable_categories()
        if self.instance.pk and self.instance.category_id:
            # Un coche de una categoria retirada conserva la suya al editarlo.
            categorias = VehicleCategory.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.category_id)
            ).order_by("sort_order", "name")
        self.fields["category"].queryset = categorias

    def clean_plate(self):
        """La matricula es un identificador: sin espacios ni guiones, en mayusculas."""
        return self.cleaned_data["plate"].replace(" ", "").replace("-", "").upper()

    def clean_vin(self):
        return self.cleaned_data["vin"].replace(" ", "").upper()


class VehicleStatusForm(forms.Form):
    """Cambio manual de estado. Solo ofrece los estados que se pueden poner."""

    status = forms.ChoiceField(label=_("Nuevo estado"), choices=[])

    def __init__(self, *args, vehicle=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.fields["status"].choices = [
            (valor, etiqueta)
            for valor, etiqueta in VehicleStatus.choices
            if valor in MANUAL_STATUSES
        ]
        if vehicle is not None:
            self.fields["status"].initial = vehicle.status


class VehicleBlockForm(forms.ModelForm):
    """Bloqueo manual de un vehiculo entre dos instantes."""

    start_at = DateTimeField(label=_("desde"))
    end_at = DateTimeField(label=_("hasta"))

    class Meta:
        model = VehicleBlock
        fields = ["vehicle", "start_at", "end_at", "reason", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        consulta = Vehicle.objects.active()
        if user is not None:
            consulta = consulta.for_user(user)
        self.fields["vehicle"].queryset = consulta.select_related("category").order_by("plate")
        self.fields["vehicle"].empty_label = _("Elige vehiculo")

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("start_at"), datos.get("end_at")
        vehiculo = datos.get("vehicle")

        if inicio and fin and fin <= inicio:
            self.add_error("end_at", _("El final tiene que ser posterior al inicio."))
            return datos

        if vehiculo and inicio and fin:
            # Aviso legible antes de llegar a la base de datos. La constraint de
            # exclusion sigue estando: es la que gana si dos usuarios guardan a
            # la vez, y esta comprobacion no puede verlo.
            solapados = VehicleBlock.objects.filter(
                vehicle=vehiculo, start_at__lt=fin, end_at__gt=inicio
            ).exclude(pk=self.instance.pk)
            if solapados.exists():
                choque = solapados.first()
                self.add_error(
                    None,
                    _("%(matricula)s ya esta bloqueado del %(desde)s al %(hasta)s (%(motivo)s).")
                    % {
                        "matricula": vehiculo.plate,
                        "desde": choque.start_at.strftime("%d/%m/%Y %H:%M"),
                        "hasta": choque.end_at.strftime("%d/%m/%Y %H:%M"),
                        "motivo": choque.get_reason_display(),
                    },
                )
        return datos


class MaintenanceRecordForm(forms.ModelForm):
    """Programar o editar un mantenimiento. Entrar y salir de taller van aparte."""

    scheduled_for = DateField(label=_("Fecha prevista"), required=False)
    next_due_date = DateField(label=_("Próximo vencimiento"), required=False)

    class Meta:
        model = MaintenanceRecord
        fields = [
            "vehicle",
            "kind",
            "scheduled_for",
            "workshop",
            "immobilizes",
            "description",
            "next_due_date",
            "next_due_km",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        consulta = Vehicle.objects.active()
        if user is not None:
            consulta = consulta.for_user(user)
        self.fields["vehicle"].queryset = consulta.order_by("plate")
        self.fields["vehicle"].label_from_instance = lambda v: f"{v.plate} · {v.brand} {v.model}"


class StartWorkshopForm(forms.Form):
    started_at = DateTimeField(label=_("Entra en taller"))
    expected_end_at = DateTimeField(label=_("Salida prevista"))

    def clean(self):
        datos = super().clean()
        if (
            datos.get("started_at")
            and datos.get("expected_end_at")
            and datos["expected_end_at"] <= datos["started_at"]
        ):
            self.add_error("expected_end_at", _("Tiene que ser posterior a la entrada."))
        return datos


class FinishWorkshopForm(forms.Form):
    finished_at = DateTimeField(label=_("Sale del taller"))
    mileage = forms.IntegerField(label=_("Kilómetros"), min_value=0, required=False)
    cost = forms.DecimalField(
        label=_("Coste"), max_digits=10, decimal_places=2, min_value=0, required=False
    )
    supplier_invoice = forms.CharField(label=_("Factura del taller"), max_length=60, required=False)
    next_due_date = DateField(
        label=_("Próximo vencimiento"),
        required=False,
        help_text=_("En una ITV, la nueva caducidad: se apunta en la ficha del coche."),
    )
    next_due_km = forms.IntegerField(label=_("Próximo a los km"), min_value=0, required=False)
