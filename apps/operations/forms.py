"""Formularios de mostrador."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateField, DateTimeField
from apps.offices.models import Office
from apps.reservations.models import ChargeKind

from .models import Damage, DamageSeverity, DamageType, DamageZone, FuelLevel, TrafficFine


class CheckInForm(forms.Form):
    """Acta de entrega."""

    actual_datetime = DateTimeField(label=_("Fecha y hora de entrega"))
    mileage = forms.IntegerField(label=_("Kilometros"), min_value=0)
    fuel_level = forms.TypedChoiceField(
        label=_("Combustible"), choices=FuelLevel.choices, coerce=int, initial=FuelLevel.FULL
    )
    observations = forms.CharField(
        label=_("Observaciones"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    licence_verified = forms.BooleanField(label=_("He comprobado el carnet de conducir"))
    id_verified = forms.BooleanField(label=_("He comprobado el DNI o pasaporte"))

    def __init__(self, *args, reservation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reservation = reservation
        # Los dos checkbox son obligatorios: sin documentos no sale el coche.
        for campo in ("licence_verified", "id_verified"):
            self.fields[campo].error_messages["required"] = _(
                "Sin comprobar los documentos no se puede entregar el coche."
            )


class CheckOutForm(forms.Form):
    """Acta de devolucion."""

    actual_datetime = DateTimeField(label=_("Fecha y hora de devolucion"))
    mileage = forms.IntegerField(label=_("Kilometros"), min_value=0)
    fuel_level = forms.TypedChoiceField(
        label=_("Combustible"), choices=FuelLevel.choices, coerce=int, initial=FuelLevel.FULL
    )
    return_office = forms.ModelChoiceField(
        label=_("Oficina de devolucion"),
        queryset=Office.objects.active().order_by("name"),
        help_text=_("Si cambia, el coche se queda aparcado alli."),
    )
    observations = forms.CharField(
        label=_("Observaciones"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    cleaning_charge = forms.DecimalField(
        label=_("Limpieza especial"),
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        help_text=_("Base imponible. En blanco, no se cobra."),
    )
    damage_charge = forms.DecimalField(
        label=_("Danos"), required=False, min_value=0, max_digits=10, decimal_places=2
    )
    damage_concept = forms.CharField(
        label=_("Concepto de los danos"), required=False, max_length=160
    )

    def __init__(self, *args, reservation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reservation = reservation
        if reservation is not None:
            entrega = getattr(reservation, "check_in", None)
            if entrega is not None:
                self.fields["mileage"].help_text = _("Al entregar marcaba %(km)s km.") % {
                    "km": entrega.mileage
                }
                self.fields["mileage"].min_value = entrega.mileage

    def clean(self):
        datos = super().clean()
        if datos.get("damage_charge") and not (datos.get("damage_concept") or "").strip():
            self.add_error("damage_concept", _("Di que dano se esta cobrando."))
        return datos

    def manual_charges(self) -> list[dict]:
        """Los cargos escritos a mano, en el formato que espera el servicio."""
        datos = self.cleaned_data
        cargos = []
        if datos.get("cleaning_charge"):
            cargos.append(
                {
                    "kind": ChargeKind.CLEANING,
                    "concept": str(_("Limpieza especial")),
                    "amount": datos["cleaning_charge"],
                }
            )
        if datos.get("damage_charge"):
            cargos.append(
                {
                    "kind": ChargeKind.DAMAGE,
                    "concept": datos["damage_concept"].strip(),
                    "amount": datos["damage_charge"],
                }
            )
        return cargos


class DamageForm(forms.ModelForm):
    """Parte de danos. La zona se elige pinchando en el croquis."""

    class Meta:
        model = Damage
        fields = [
            "zone",
            "damage_type",
            "severity",
            "description",
            "estimated_cost",
            "charge_to_customer",
        ]
        widgets = {
            "zone": forms.HiddenInput,
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, preexisting=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.preexisting = preexisting
        self.fields["zone"].choices = DamageZone.choices
        self.fields["damage_type"].choices = DamageType.choices
        self.fields["severity"].choices = DamageSeverity.choices
        if preexisting:
            # Lo que ya estaba no se le cobra al cliente de hoy.
            self.fields["charge_to_customer"].disabled = True
            self.fields["charge_to_customer"].initial = False
            self.fields["estimated_cost"].help_text = _(
                "Solo a efectos de inventario: un dano previo no se repercute."
            )

    def clean_zone(self):
        zona = self.cleaned_data.get("zone")
        if not zona:
            raise forms.ValidationError(_("Pincha la zona danada en el croquis."))
        return zona


class TrafficFineForm(forms.ModelForm):
    """Datos de la notificacion. La reserva y el conductor los pone el servicio."""

    offense_at = DateTimeField(label=_("Fecha y hora de la infracción"))
    notified_on = DateField(label=_("Fecha de notificación"), required=False)
    identify_by = DateField(
        label=_("Plazo para identificar"),
        required=False,
        help_text=_("Vacío: 20 días naturales desde la notificación."),
    )

    class Meta:
        model = TrafficFine
        fields = [
            "vehicle",
            "offense_at",
            "place",
            "authority",
            "file_number",
            "description",
            "amount",
            "notified_on",
            "identify_by",
            "notes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        from apps.fleet.models import Vehicle
        from apps.offices.selectors import offices_for_user

        super().__init__(*args, **kwargs)
        # Solo coches de las oficinas del usuario; los de baja tambien, porque la
        # multa puede llegar meses despues de retirarlos.
        self.fields["vehicle"].queryset = Vehicle.objects.filter(
            current_office__in=offices_for_user(user)
        ).order_by("plate")
        self.fields["vehicle"].label_from_instance = lambda v: f"{v.plate} · {v.brand} {v.model}"
