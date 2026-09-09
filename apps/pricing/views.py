"""Pantallas de tarifas.

Todo lo que el administrador necesita para poner precios sin tocar codigo:
temporadas, tarifas con sus tramos, suplementos, descuentos, un simulador que
ensena el precio **y por que sale ese precio**, y una pantalla de conflictos
que avisa de dos tarifas que se pisan antes de que reviente en el mostrador.

Mismo patron CRUD que el resto (docs/patrones/crud.md).
"""

import json

import structlog
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.crud import (
    EVENTO_GUARDADO,
    CrudListView,
    CrudPermissionMixin,
    ModalCreateView,
    ModalUpdateView,
    ToggleActiveView,
)
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.tables import Column

from .filters import DiscountFilter, ExtraFilter, RateFilter, SeasonFilter, SupplementFilter
from .forms import (
    DiscountForm,
    ExtraForm,
    PriceSimulatorForm,
    RateForm,
    RateTierFormSet,
    SeasonForm,
    SupplementForm,
)
from .models import Discount, Extra, Rate, Season, Supplement
from .services import (
    PricingError,
    TierSpec,
    calculate_reservation_price,
    coverage_summary,
    duplicate_rate,
    find_rate_conflicts,
    save_discount,
    save_extra,
    save_rate,
    save_season,
    save_supplement,
    set_discount_active,
    set_extra_active,
    set_rate_active,
    set_season_active,
    set_supplement_active,
    validate_tiers,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------


class ExtraListView(CrudListView):
    permission_required = "pricing.view_extra"
    model = Extra
    filterset_class = ExtraFilter
    table_id = "tabla-extras"
    table_row_template = "pricing/_extra_row.html"
    table_columns = [
        Column(label=_("Orden"), css="tabular w-20"),
        Column(label=_("Codigo"), css="font-mono tabular w-28"),
        Column(label=_("Extra")),
        Column(label=_("Cobro")),
        Column(label=_("Precio"), align="right", css="tabular w-28"),
        Column(label=_("Tope"), align="right", css="tabular w-28"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ningun extra coincide")
    empty_message = _("Los extras son los suplementos que se anaden a una reserva.")
    page_title = _("Extras")
    create_url_name = "pricing:extra_create"
    create_label = _("Nuevo extra")
    create_permission = "pricing.add_extra"


class ExtraFormMixin:
    model = Extra
    form_class = ExtraForm
    table_id = "tabla-extras"
    list_url_name = "pricing:extra_list"

    def save_object(self, form):
        return save_extra(extra=form.save(commit=False), actor=self.request.user)


class ExtraCreateView(ExtraFormMixin, ModalCreateView):
    permission_required = "pricing.add_extra"
    modal_title = _("Nuevo extra")
    submit_label = _("Crear extra")
    success_message = _("Extra %(objeto)s creado.")


class ExtraUpdateView(ExtraFormMixin, ModalUpdateView):
    permission_required = "pricing.change_extra"
    modal_title = _("Editar extra")
    success_message = _("Extra %(objeto)s actualizado.")


class ExtraToggleView(ToggleActiveView):
    permission_required = "pricing.change_extra"
    model = Extra
    list_url_name = "pricing:extra_list"
    activated_message = _("Extra %(objeto)s de vuelta en el catalogo.")
    deactivated_message = _("Extra %(objeto)s retirado del catalogo.")

    def perform(self, objeto):
        set_extra_active(extra=objeto, active=self.activate, actor=self.request.user)


class ExtraActivateView(ExtraToggleView):
    activate = True


class ExtraDeactivateView(ExtraToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


class SeasonListView(CrudListView):
    permission_required = "pricing.view_season"
    model = Season
    filterset_class = SeasonFilter
    table_id = "tabla-temporadas"
    table_row_template = "pricing/_season_row.html"
    table_columns = [
        Column(label=_("Codigo"), css="font-mono tabular w-32"),
        Column(label=_("Temporada")),
        Column(label=_("Desde"), css="tabular"),
        Column(label=_("Hasta"), css="tabular"),
        Column(label=_("Prioridad"), align="right", css="tabular w-24"),
        Column(label=_("Tarifas"), align="right", css="tabular w-20"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ninguna temporada coincide")
    empty_message = _("Las temporadas pueden solaparse: gana la de mas prioridad.")
    page_title = _("Temporadas")
    create_url_name = "pricing:season_create"
    create_label = _("Nueva temporada")
    create_permission = "pricing.add_season"

    def get_base_queryset(self):
        from django.db.models import Count

        # El order_by explicito no sobra: con un annotate, Django deja de dar
        # por buena la ordenacion del Meta y la paginacion sale inestable.
        return (
            super()
            .get_base_queryset()
            .annotate(num_rates=Count("rates"))
            .order_by("-priority", "start_date")
        )


class SeasonFormMixin:
    model = Season
    form_class = SeasonForm
    table_id = "tabla-temporadas"
    list_url_name = "pricing:season_list"

    def save_object(self, form):
        return save_season(season=form.save(commit=False), actor=self.request.user)


class SeasonCreateView(SeasonFormMixin, ModalCreateView):
    permission_required = "pricing.add_season"
    modal_title = _("Nueva temporada")
    submit_label = _("Crear temporada")
    success_message = _("Temporada %(objeto)s creada.")


class SeasonUpdateView(SeasonFormMixin, ModalUpdateView):
    permission_required = "pricing.change_season"
    modal_title = _("Editar temporada")
    success_message = _("Temporada %(objeto)s actualizada.")


class SeasonToggleView(ToggleActiveView):
    permission_required = "pricing.change_season"
    model = Season
    list_url_name = "pricing:season_list"
    activated_message = _("Temporada %(objeto)s reactivada.")
    deactivated_message = _("Temporada %(objeto)s desactivada.")

    def perform(self, objeto):
        set_season_active(season=objeto, active=self.activate, actor=self.request.user)


class SeasonActivateView(SeasonToggleView):
    activate = True


class SeasonDeactivateView(SeasonToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------


class RateListView(CrudListView):
    permission_required = "pricing.view_rate"
    model = Rate
    filterset_class = RateFilter
    table_id = "tabla-tarifas"
    table_row_template = "pricing/_rate_row.html"
    table_columns = [
        Column(label=_("Codigo"), css="font-mono tabular w-36"),
        Column(label=_("Tarifa")),
        Column(label=_("Canal")),
        Column(label=_("Temporada")),
        Column(label=_("Tramos")),
        Column(label=_("Prioridad"), align="right", css="tabular w-24"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ninguna tarifa coincide")
    empty_message = _("Una tarifa son sus tramos: sin ellos no hay precio.")
    page_title = _("Tarifas")
    create_url_name = "pricing:rate_create"
    create_label = _("Nueva tarifa")
    create_permission = "pricing.add_rate"

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .select_related("season")
            .prefetch_related("tiers", "categories", "offices")
        )


class RateFormMixin:
    model = Rate
    form_class = RateForm
    formset_class = RateTierFormSet
    formset_template = "pricing/_tier_editor.html"
    modal_width = "max-w-3xl"
    table_id = "tabla-tarifas"
    list_url_name = "pricing:rate_list"

    def save_object(self, form):
        # La tarifa y sus tramos, en la misma transaccion: el servicio los
        # revalida como conjunto antes de dar el guardado por bueno.
        return save_rate(
            rate=form.save(commit=False), formset=self.get_formset(), actor=self.request.user
        )


class RateCreateView(RateFormMixin, ModalCreateView):
    permission_required = "pricing.add_rate"
    modal_title = _("Nueva tarifa")
    submit_label = _("Crear tarifa")
    success_message = _("Tarifa %(objeto)s creada.")


class RateUpdateView(RateFormMixin, ModalUpdateView):
    permission_required = "pricing.change_rate"
    modal_title = _("Editar tarifa")
    success_message = _("Tarifa %(objeto)s actualizada.")


class RateToggleView(ToggleActiveView):
    permission_required = "pricing.change_rate"
    model = Rate
    list_url_name = "pricing:rate_list"
    activated_message = _("Tarifa %(objeto)s activada.")
    deactivated_message = _("Tarifa %(objeto)s desactivada.")

    def perform(self, objeto):
        set_rate_active(rate=objeto, active=self.activate, actor=self.request.user)


class RateActivateView(RateToggleView):
    activate = True


class RateDeactivateView(RateToggleView):
    activate = False


class RateDuplicateView(CrudPermissionMixin, View):
    """Duplica una tarifa y abre la copia para ajustarla.

    Es como se monta la temporada alta: se copia la baja, se le cambia la
    temporada y se suben los precios. La copia nace desactivada, y el modal de
    edicion se abre solo (evento `crud:abrir-modal`) para no tener que buscarla.
    """

    permission_required = "pricing.add_rate"

    def post(self, request, pk, *args, **kwargs):
        original = get_object_or_404(Rate.objects.all(), pk=pk)
        copia = duplicate_rate(rate=original, actor=request.user)
        mensaje = _("Tarifa duplicada como %(codigo)s. Esta desactivada hasta que la repases.") % {
            "codigo": copia.code
        }

        if not request.htmx:
            messages.success(request, mensaje)
            return HttpResponseRedirect(reverse("pricing:rate_list"))

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        trigger_event(
            respuesta,
            "crud:abrir-modal",
            {"url": reverse("pricing:rate_update", args=[copia.pk])},
        )
        return trigger_toast(respuesta, mensaje, "success")


class RateTierCheckView(CrudPermissionMixin, View):
    """Aviso en vivo del editor de tramos.

    Recibe lo que hay tecleado ahora mismo y devuelve el estado de la cobertura.
    No guarda nada y no sustituye a la validacion del guardado: es para que el
    hueco del dia 4 se vea **mientras** se escribe, no al pulsar Guardar.
    """

    permission_required = "pricing.view_rate"

    def post(self, request, *args, **kwargs):
        especificaciones, incompletos = self._leer_tramos(request.POST)
        problemas = validate_tiers(especificaciones) if especificaciones else []

        return render(
            request,
            "pricing/_tier_feedback.html",
            {
                "problemas": problemas,
                "cobertura": coverage_summary(especificaciones),
                "incompletos": incompletos,
                "sin_tramos": not especificaciones,
            },
        )

    @staticmethod
    def _leer_tramos(datos):
        """Saca los tramos del POST del formset, tolerando filas a medias."""
        total = int(datos.get("tiers-TOTAL_FORMS") or 0)
        especificaciones, incompletos = [], 0
        for indice in range(total):
            prefijo = f"tiers-{indice}"
            if datos.get(f"{prefijo}-DELETE"):
                continue
            desde = datos.get(f"{prefijo}-min_days", "").strip()
            hasta = datos.get(f"{prefijo}-max_days", "").strip()
            precio = datos.get(f"{prefijo}-price_per_day", "").strip()
            if not desde and not hasta and not precio:
                continue  # fila vacia: aun no la han tocado
            if not desde or not precio:
                incompletos += 1
                continue
            try:
                especificaciones.append(
                    TierSpec(
                        min_days=int(desde),
                        max_days=int(hasta) if hasta else None,
                        price_per_day=precio,
                    )
                )
            except ValueError:
                incompletos += 1
        return especificaciones, incompletos


# ---------------------------------------------------------------------------
# Suplementos
# ---------------------------------------------------------------------------


class SupplementListView(CrudListView):
    permission_required = "pricing.view_supplement"
    model = Supplement
    filterset_class = SupplementFilter
    table_id = "tabla-suplementos"
    table_row_template = "pricing/_supplement_row.html"
    table_columns = [
        Column(label=_("Codigo"), css="font-mono tabular w-36"),
        Column(label=_("Suplemento")),
        Column(label=_("Cuando se aplica")),
        Column(label=_("Importe"), align="right", css="tabular w-28"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ningun suplemento coincide")
    empty_message = _("Un suplemento se cobra solo cuando se cumple su condicion.")
    page_title = _("Suplementos")
    create_url_name = "pricing:supplement_create"
    create_label = _("Nuevo suplemento")
    create_permission = "pricing.add_supplement"

    def get_base_queryset(self):
        return super().get_base_queryset().prefetch_related("offices")


class SupplementFormMixin:
    model = Supplement
    form_class = SupplementForm
    table_id = "tabla-suplementos"
    list_url_name = "pricing:supplement_list"
    modal_width = "max-w-2xl"

    def save_object(self, form):
        return save_supplement(supplement=form.save(commit=False), actor=self.request.user)


class SupplementCreateView(SupplementFormMixin, ModalCreateView):
    permission_required = "pricing.add_supplement"
    modal_title = _("Nuevo suplemento")
    submit_label = _("Crear suplemento")
    success_message = _("Suplemento %(objeto)s creado.")


class SupplementUpdateView(SupplementFormMixin, ModalUpdateView):
    permission_required = "pricing.change_supplement"
    modal_title = _("Editar suplemento")
    success_message = _("Suplemento %(objeto)s actualizado.")


class SupplementToggleView(ToggleActiveView):
    permission_required = "pricing.change_supplement"
    model = Supplement
    list_url_name = "pricing:supplement_list"
    activated_message = _("Suplemento %(objeto)s activado.")
    deactivated_message = _("Suplemento %(objeto)s desactivado.")

    def perform(self, objeto):
        set_supplement_active(supplement=objeto, active=self.activate, actor=self.request.user)


class SupplementActivateView(SupplementToggleView):
    activate = True


class SupplementDeactivateView(SupplementToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Descuentos
# ---------------------------------------------------------------------------


class DiscountListView(CrudListView):
    permission_required = "pricing.view_discount"
    model = Discount
    filterset_class = DiscountFilter
    table_id = "tabla-descuentos"
    table_row_template = "pricing/_discount_row.html"
    table_columns = [
        Column(label=_("Descuento")),
        Column(label=_("Codigo"), css="font-mono tabular w-36"),
        Column(label=_("Importe"), align="right", css="tabular w-28"),
        Column(label=_("Vigencia")),
        Column(label=_("Condiciones")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Nombre o codigo...")
    empty_title = _("Ningun descuento coincide")
    empty_message = _("Sin codigo se aplican solos; con codigo hay que teclearlos.")
    page_title = _("Descuentos")
    create_url_name = "pricing:discount_create"
    create_label = _("Nuevo descuento")
    create_permission = "pricing.add_discount"

    def get_base_queryset(self):
        return super().get_base_queryset().prefetch_related("categories")


class DiscountFormMixin:
    model = Discount
    form_class = DiscountForm
    table_id = "tabla-descuentos"
    list_url_name = "pricing:discount_list"
    modal_width = "max-w-2xl"

    def save_object(self, form):
        return save_discount(discount=form.save(commit=False), actor=self.request.user)


class DiscountCreateView(DiscountFormMixin, ModalCreateView):
    permission_required = "pricing.add_discount"
    modal_title = _("Nuevo descuento")
    submit_label = _("Crear descuento")
    success_message = _("Descuento %(objeto)s creado.")


class DiscountUpdateView(DiscountFormMixin, ModalUpdateView):
    permission_required = "pricing.change_discount"
    modal_title = _("Editar descuento")
    success_message = _("Descuento %(objeto)s actualizado.")


class DiscountToggleView(ToggleActiveView):
    permission_required = "pricing.change_discount"
    model = Discount
    list_url_name = "pricing:discount_list"
    activated_message = _("Descuento %(objeto)s activado.")
    deactivated_message = _("Descuento %(objeto)s desactivado.")

    def perform(self, objeto):
        set_discount_active(discount=objeto, active=self.activate, actor=self.request.user)


class DiscountActivateView(DiscountToggleView):
    activate = True


class DiscountDeactivateView(DiscountToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Simulador y conflictos
# ---------------------------------------------------------------------------


class PriceSimulatorView(CrudPermissionMixin, View):
    """Calcula un precio de mentira con la configuracion de verdad.

    Es la pantalla que hace que el cliente se fie de lo que ha configurado: no
    ensena solo el total, ensena **que tarifa y que tramo** se han aplicado, que
    es justo lo que hay que revisar cuando el precio no es el que se esperaba.
    """

    permission_required = "pricing.view_rate"
    template_name = "pricing/simulator.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context(PriceSimulatorForm()))

    def post(self, request, *args, **kwargs):
        form = PriceSimulatorForm(request.POST)
        contexto = self.get_context(form)

        if form.is_valid():
            try:
                contexto["breakdown"] = calculate_reservation_price(form.as_quote())
            except PricingError as exc:
                # Un fallo de configuracion no es un 500: es justo el resultado
                # que el administrador ha venido a ver.
                contexto["pricing_error"] = str(exc)
                contexto["pricing_error_type"] = type(exc).__name__
            else:
                contexto["breakdown_json"] = json.dumps(
                    contexto["breakdown"].to_dict(), indent=2, ensure_ascii=False
                )

        if request.htmx:
            return render(request, "pricing/_simulator_result.html", contexto)
        return render(request, self.template_name, contexto)

    def get_context(self, form):
        return {
            "form": form,
            "page_title": _("Simulador de precios"),
            "page_subtitle": _("Calcula con la configuracion real, sin crear ninguna reserva."),
            "breadcrumbs": [
                {"label": _("Inicio"), "url": reverse("core:home")},
                {"label": _("Tarifas"), "url": reverse("pricing:rate_list")},
                {"label": _("Simulador")},
            ],
        }


class RateConflictListView(CrudPermissionMixin, TemplateView):
    """Tarifas que competirian por la misma venta.

    Lo que aqui sale como aviso, en el mostrador saldria como `AmbiguousRate`
    con un cliente delante. Mejor verlo hoy.
    """

    permission_required = "pricing.view_rate"
    template_name = "pricing/conflicts.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["conflicts"] = find_rate_conflicts()
        contexto["page_title"] = _("Conflictos entre tarifas")
        contexto["page_subtitle"] = _(
            "Dos tarifas igual de aplicables dejan al motor sin forma de elegir."
        )
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Tarifas"), "url": reverse("pricing:rate_list")},
            {"label": _("Conflictos")},
        ]
        return contexto
