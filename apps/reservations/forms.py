"""Formularios de reserva."""

from datetime import timedelta

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateInput, DateTimeField
from apps.customers.models import Customer, DocumentType
from apps.fleet.models import VehicleCategory
from apps.offices.models import Office
from apps.pricing.models import Extra

from .models import CancellationPolicy, FuelPolicy


class QuickReservationForm(forms.Form):
    """Alta de mostrador. Lo minimo para cerrar una reserva de pie.

    La devolucion se puede dar como fecha o como numero de dias, que es como se
    habla en el mostrador ("una semana"). Si vienen las dos, manda la fecha.
    """

    customer = forms.ModelChoiceField(
        label=_("Cliente"),
        queryset=Customer.objects.active(),
        widget=forms.HiddenInput,
        error_messages={"required": _("Elige un cliente o dalo de alta.")},
    )
    category = forms.ModelChoiceField(
        label=_("Categoria"),
        queryset=VehicleCategory.objects.active().order_by("sort_order", "name"),
        empty_label=None,
    )
    pickup_office = forms.ModelChoiceField(
        label=_("Oficina de recogida"), queryset=Office.objects.active().order_by("name")
    )
    return_office = forms.ModelChoiceField(
        label=_("Oficina de devolucion"),
        queryset=Office.objects.active().order_by("name"),
        required=False,
        help_text=_("En blanco: se devuelve en la misma oficina."),
    )

    pickup_at = DateTimeField(label=_("Recogida"))
    days = forms.IntegerField(
        label=_("Dias"),
        min_value=1,
        max_value=365,
        required=False,
        help_text=_("O indica la fecha de devolucion."),
    )
    return_at = DateTimeField(label=_("Devolucion"), required=False)

    extras = forms.ModelMultipleChoiceField(
        label=_("Extras"),
        queryset=Extra.objects.active().order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    fuel_policy = forms.ChoiceField(
        label=_("Combustible"), choices=FuelPolicy.choices, initial=FuelPolicy.FULL_FULL
    )
    cancellation_policy = forms.ChoiceField(
        label=_("Cancelacion"),
        choices=CancellationPolicy.choices,
        initial=CancellationPolicy.FLEXIBLE,
    )
    notes = forms.CharField(
        label=_("Notas"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None and not user.is_superuser:
            # Solo se reserva desde las oficinas propias.
            propias = user.offices.filter(is_active=True)
            self.fields["pickup_office"].queryset = propias
            self.fields["return_office"].queryset = Office.objects.active().order_by("name")

    def clean(self):
        datos = super().clean()
        recogida = datos.get("pickup_at")
        devolucion = datos.get("return_at")
        dias = datos.get("days")

        if recogida and not devolucion:
            if not dias:
                self.add_error("days", _("Dime los dias o la fecha de devolucion."))
            else:
                devolucion = recogida + timedelta(days=dias)
                datos["return_at"] = devolucion

        if recogida and devolucion and devolucion <= recogida:
            self.add_error("return_at", _("La devolucion tiene que ser posterior a la recogida."))

        if not datos.get("return_office"):
            datos["return_office"] = datos.get("pickup_office")

        return datos


class QuickCustomerForm(forms.ModelForm):
    """Alta de cliente al vuelo, con lo justo para poder alquilar.

    La ficha completa se rellena despues; aqui lo que no puede faltar es a quien
    se le entrega el coche y como localizarlo.
    """

    class Meta:
        model = Customer
        fields = ["first_name", "last_name", "document_type", "document_number", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document_type"].initial = DocumentType.DNI
        for nombre in ("first_name", "last_name", "document_number", "phone"):
            self.fields[nombre].required = True


class TransitionForm(forms.Form):
    """Motivo de un cambio de estado. Algunas transiciones no van sin el."""

    reason = forms.CharField(
        label=_("Motivo"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, requires_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = requires_reason
        if requires_reason:
            self.fields["reason"].help_text = _("Obligatorio para esta operacion.")


class ChangeDatesForm(forms.Form):
    """Cambio de fechas de una reserva ya creada."""

    pickup_at = DateTimeField(label=_("Nueva recogida"))
    return_at = DateTimeField(label=_("Nueva devolucion"))

    def clean(self):
        datos = super().clean()
        recogida, devolucion = datos.get("pickup_at"), datos.get("return_at")
        if recogida and devolucion and devolucion <= recogida:
            self.add_error("return_at", _("La devolucion tiene que ser posterior a la recogida."))
        return datos


class ChangeCategoryForm(forms.Form):
    category = forms.ModelChoiceField(
        label=_("Nueva categoria"),
        queryset=VehicleCategory.objects.active().order_by("sort_order", "name"),
        empty_label=None,
    )


class DriverForm(forms.ModelForm):
    """Conductor adicional. El carnet se valida contra las fechas del alquiler."""

    class Meta:
        from .models import ReservationDriver

        model = ReservationDriver
        fields = [
            "first_name",
            "last_name",
            "birth_date",
            "document_number",
            "licence_number",
            "licence_country",
            "licence_issued_on",
            "licence_expiry",
        ]
        widgets = {
            "birth_date": DateInput,
            "licence_issued_on": DateInput,
            "licence_expiry": DateInput,
        }

    def __init__(self, *args, reservation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reservation = reservation
        self.fields["licence_expiry"].required = True

    def clean(self):
        datos = super().clean()
        if self.reservation is None:
            return datos

        from .services import ReservationServiceError, validate_licence

        borrador = self.instance
        for campo, valor in datos.items():
            setattr(borrador, campo, valor)
        try:
            validate_licence(driver=borrador, reservation=self.reservation)
        except ReservationServiceError as exc:
            self.add_error("licence_expiry", str(exc))
        return datos


class AddExtraForm(forms.Form):
    """Anadir un extra a una reserva ya creada."""

    extra = forms.ModelChoiceField(
        label=_("Extra"),
        queryset=Extra.objects.active().order_by("name"),
        empty_label=None,
    )
    quantity = forms.IntegerField(label=_("Cantidad"), min_value=1, initial=1)

    def __init__(self, *args, reservation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reservation = reservation
        if reservation is not None:
            # Los que ya estan no se ofrecen otra vez: se cambia su cantidad.
            puestos = reservation.extras.values_list("extra_id", flat=True)
            self.fields["extra"].queryset = Extra.objects.active().exclude(pk__in=puestos)

    def clean(self):
        datos = super().clean()
        extra, cantidad = datos.get("extra"), datos.get("quantity")
        if extra and cantidad and extra.max_quantity and cantidad > extra.max_quantity:
            self.add_error(
                "quantity",
                _("De %(extra)s no se pueden poner mas de %(tope)s.")
                % {"extra": extra.name, "tope": extra.max_quantity},
            )
        return datos


class ManualPriceForm(forms.Form):
    """Precio del alquiler acordado a mano. Motivo obligatorio."""

    daily_price = forms.DecimalField(
        label=_("Precio por dia"),
        min_value=0,
        max_digits=10,
        decimal_places=2,
        help_text=_("Base imponible del alquiler por dia. Los extras siguen su tarifa."),
    )
    reason = forms.CharField(
        label=_("Motivo"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Queda registrado con tu nombre. Sin motivo no se guarda."),
    )

    def clean_reason(self):
        motivo = self.cleaned_data["reason"].strip()
        if not motivo:
            raise forms.ValidationError(_("Explica por que se cambia el precio."))
        return motivo


class PlanningForm(forms.Form):
    """Filtros del planning. Todo opcional: sin nada, dos semanas desde hoy."""

    desde = forms.DateField(label=_("Desde"), required=False, widget=DateInput())
    dias = forms.TypedChoiceField(
        label=_("Dias"),
        coerce=int,
        required=False,
        choices=[(7, _("7 dias")), (14, _("14 dias")), (31, _("31 dias"))],
    )
    office = forms.ModelChoiceField(
        label=_("Oficina"), queryset=Office.objects.none(), required=False
    )
    category = forms.ModelChoiceField(
        label=_("Categoria"), queryset=VehicleCategory.objects.none(), required=False
    )

    def __init__(self, *args, user=None, **kwargs):
        from apps.offices.selectors import offices_for_user

        super().__init__(*args, **kwargs)
        # Solo las oficinas del usuario: una ajena por la URL no valida.
        self.fields["office"].queryset = offices_for_user(user).order_by("name")
        self.fields["office"].empty_label = _("Todas mis oficinas")
        self.fields["category"].queryset = VehicleCategory.objects.active().order_by(
            "sort_order", "name"
        )
        self.fields["category"].empty_label = _("Todas las categorias")
