"""Formularios de tarifas y extras."""

from django import forms
from django.forms.models import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateInput, DateTimeField
from apps.fleet.models import VehicleCategory
from apps.offices.models import Office

from .dto import ExtraRequest, PriceQuoteInput
from .models import (
    AmountType,
    CalculationType,
    Channel,
    Discount,
    Extra,
    Rate,
    RateTier,
    Season,
    Supplement,
    SupplementType,
)
from .services import TierSpec, validate_tiers


class ExtraForm(forms.ModelForm):
    class Meta:
        model = Extra
        fields = [
            "code",
            "name",
            "description",
            "calculation_type",
            "price",
            "tax_rate",
            "max_quantity",
            "max_amount",
            "requires_driver_data",
            "sort_order",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean(self):
        """El tope solo tiene sentido cobrando por dia.

        Un extra de precio unico con "importe maximo" es una contradiccion que
        acabaria en una discusion en el mostrador sobre cuanto se cobra.
        """
        datos = super().clean()
        if datos.get("max_amount") is not None:
            if datos.get("calculation_type") != CalculationType.PER_DAY:
                self.add_error(
                    "max_amount",
                    _("El importe maximo solo se aplica a los extras que se cobran por dia."),
                )
            elif datos.get("price") is not None and datos["max_amount"] < datos["price"]:
                self.add_error(
                    "max_amount",
                    _("El tope no puede ser menor que el precio de un solo dia."),
                )
        return datos


class SeasonForm(forms.ModelForm):
    start_date = forms.DateField(label=_("desde"), widget=DateInput)
    end_date = forms.DateField(label=_("hasta"), widget=DateInput)

    class Meta:
        model = Season
        fields = ["code", "name", "start_date", "end_date", "priority"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("start_date"), datos.get("end_date")
        if desde and hasta and hasta < desde:
            self.add_error("end_date", _("La temporada no puede acabar antes de empezar."))
        return datos


class RateForm(forms.ModelForm):
    valid_from = forms.DateField(label=_("vigente desde"), required=False, widget=DateInput)
    valid_to = forms.DateField(label=_("vigente hasta"), required=False, widget=DateInput)

    class Meta:
        model = Rate
        fields = [
            "code",
            "name",
            "channel",
            "categories",
            "offices",
            "season",
            "valid_from",
            "valid_to",
            "priority",
            "tier_mode",
            "included_km_per_day",
            "extra_km_price",
            "min_days",
            "max_days",
        ]
        widgets = {
            "categories": forms.CheckboxSelectMultiple,
            "offices": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].queryset = VehicleCategory.objects.active().order_by(
            "sort_order", "name"
        )
        self.fields["offices"].queryset = Office.objects.active().order_by("name")
        self.fields["offices"].help_text = _("Sin marcar ninguna: la tarifa vale en todas.")
        self.fields["season"].queryset = Season.objects.active().order_by("-priority", "name")

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("valid_from"), datos.get("valid_to")
        if desde and hasta and hasta < desde:
            self.add_error("valid_to", _("La vigencia no puede acabar antes de empezar."))

        min_dias, max_dias = datos.get("min_days"), datos.get("max_days")
        if min_dias and max_dias and max_dias < min_dias:
            self.add_error("max_days", _("El maximo de dias no puede ser menor que el minimo."))
        return datos


class RateTierBaseFormSet(BaseInlineFormSet):
    """Los tramos de una tarifa, validados **como conjunto**.

    Un tramo suelto casi siempre es correcto; lo que falla es la lista: un
    hueco en el dia 4, dos tramos que se pisan, un ultimo tramo cerrado. Por eso
    la comprobacion vive aqui y no en el formulario de cada fila.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return  # con filas mal rellenadas, el conjunto no se puede juzgar

        especificaciones = []
        for formulario in self.forms:
            if not formulario.cleaned_data or formulario.cleaned_data.get("DELETE"):
                continue
            especificaciones.append(
                TierSpec(
                    min_days=formulario.cleaned_data["min_days"],
                    max_days=formulario.cleaned_data.get("max_days"),
                    price_per_day=formulario.cleaned_data.get("price_per_day"),
                )
            )

        problemas = validate_tiers(especificaciones)
        if problemas:
            raise forms.ValidationError(problemas)


RateTierFormSet = inlineformset_factory(
    Rate,
    RateTier,
    formset=RateTierBaseFormSet,
    fields=["min_days", "max_days", "price_per_day"],
    extra=2,
    can_delete=True,
)


class SupplementForm(forms.ModelForm):
    class Meta:
        model = Supplement
        fields = [
            "code",
            "name",
            "supplement_type",
            "amount_type",
            "amount",
            "tax_rate",
            "min_age",
            "max_age",
            "offices",
            "hours_from",
            "hours_to",
            "sort_order",
        ]
        widgets = {
            "offices": forms.CheckboxSelectMultiple,
            "hours_from": forms.TimeInput(attrs={"type": "time"}),
            "hours_to": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offices"].queryset = Office.objects.active().order_by("name")

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean(self):
        """Cada tipo de suplemento necesita lo suyo, y sin ello no se aplicaria.

        Un suplemento que nunca se aplica es peor que no tenerlo: parece que
        esta cobrando y no cobra nada.
        """
        datos = super().clean()
        tipo = datos.get("supplement_type")

        if tipo == SupplementType.YOUNG_DRIVER and datos.get("max_age") is None:
            self.add_error("max_age", _("Sin edad maxima, el suplemento joven no se aplica nunca."))

        if tipo == SupplementType.AFTER_HOURS and (
            not datos.get("hours_from") or not datos.get("hours_to")
        ):
            self.add_error(
                "hours_from", _("Indica el horario de la oficina, de apertura a cierre.")
            )

        if tipo == SupplementType.AIRPORT and not datos.get("offices"):
            self.add_error("offices", _("Marca las oficinas de aeropuerto que cobran la tasa."))

        desde, hasta = datos.get("min_age"), datos.get("max_age")
        if desde is not None and hasta is not None and hasta < desde:
            self.add_error("max_age", _("La edad maxima no puede ser menor que la minima."))
        return datos


class DiscountForm(forms.ModelForm):
    valid_from = forms.DateField(label=_("vigente desde"), required=False, widget=DateInput)
    valid_to = forms.DateField(label=_("vigente hasta"), required=False, widget=DateInput)

    class Meta:
        model = Discount
        fields = [
            "name",
            "code",
            "amount_type",
            "amount",
            "valid_from",
            "valid_to",
            "min_days",
            "categories",
        ]
        widgets = {"categories": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].queryset = VehicleCategory.objects.active().order_by(
            "sort_order", "name"
        )
        self.fields["code"].help_text = _(
            "Vacio: se aplica solo cuando se cumplen sus condiciones, sin teclear nada."
        )

    def clean_code(self):
        return (self.cleaned_data["code"] or "").strip().lower()

    def clean(self):
        datos = super().clean()
        if datos.get("amount_type") == AmountType.PERCENT and datos.get("amount", 0) > 100:
            self.add_error("amount", _("Un porcentaje no pasa del 100%."))
        desde, hasta = datos.get("valid_from"), datos.get("valid_to")
        if desde and hasta and hasta < desde:
            self.add_error("valid_to", _("La vigencia no puede acabar antes de empezar."))
        return datos


class PriceSimulatorForm(forms.Form):
    """Consulta del simulador de precios.

    No guarda nada: monta un `PriceQuoteInput` y se lo pasa al motor. Es la
    pantalla que hace que el administrador se fie de lo que ha configurado,
    porque le ensena el precio **y** por que sale ese precio.
    """

    category = forms.ModelChoiceField(
        label=_("Categoria"),
        queryset=VehicleCategory.objects.none(),
        empty_label=None,
    )
    pickup_office = forms.ModelChoiceField(
        label=_("Oficina de recogida"), queryset=Office.objects.none(), empty_label=None
    )
    return_office = forms.ModelChoiceField(
        label=_("Oficina de devolucion"), queryset=Office.objects.none(), empty_label=None
    )
    pickup_at = DateTimeField(label=_("Recogida"))
    return_at = DateTimeField(label=_("Devolucion"))
    channel = forms.ChoiceField(label=_("Canal"), choices=Channel.choices)
    customer_age = forms.IntegerField(
        label=_("Edad del conductor"),
        required=False,
        min_value=16,
        max_value=110,
        help_text=_("Para comprobar el suplemento de conductor joven."),
    )
    extras = forms.ModelMultipleChoiceField(
        label=_("Extras"),
        queryset=Extra.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    discount_code = forms.CharField(label=_("Codigo de descuento"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = VehicleCategory.objects.active().order_by(
            "sort_order", "name"
        )
        oficinas = Office.objects.active().order_by("name")
        self.fields["pickup_office"].queryset = oficinas
        self.fields["return_office"].queryset = oficinas
        self.fields["extras"].queryset = Extra.objects.active().order_by("sort_order", "name")

    def clean(self):
        datos = super().clean()
        salida, vuelta = datos.get("pickup_at"), datos.get("return_at")
        if salida and vuelta and vuelta <= salida:
            self.add_error("return_at", _("La devolucion tiene que ser posterior a la recogida."))
        return datos

    def as_quote(self) -> PriceQuoteInput:
        """Traduce el formulario al DTO que entiende el motor."""
        datos = self.cleaned_data
        return PriceQuoteInput(
            category=datos["category"],
            pickup_office=datos["pickup_office"],
            return_office=datos["return_office"],
            pickup_at=datos["pickup_at"],
            return_at=datos["return_at"],
            channel=datos["channel"],
            extras=tuple(ExtraRequest(extra, 1) for extra in datos.get("extras", [])),
            customer_age=datos.get("customer_age"),
            discount_code=datos.get("discount_code", ""),
        )
