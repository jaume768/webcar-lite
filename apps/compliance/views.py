"""Pantallas de cumplimiento: los partes a SES.Hospedajes."""

import django_filters as filters
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.crud import CrudListView, CrudPermissionMixin
from apps.core.services import ServiceError
from apps.core.tables import Column

from .models import SesStatus, SesSubmission
from .services import prepare, send


class SesFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    status = filters.ChoiceFilter(
        label=_("Estado"), choices=SesStatus.choices, empty_label=_("Estado: todos")
    )

    class Meta:
        model = SesSubmission
        fields = ["q", "status"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(reservation__number__icontains=termino)
            | Q(reservation__customer__search_text__contains=termino.lower())
            | Q(lot_code__icontains=termino)
        )


class SesListView(CrudListView):
    permission_required = "compliance.view_sessubmission"
    model = SesSubmission
    filterset_class = SesFilter
    table_id = "tabla-ses"
    table_row_template = "compliance/_ses_row.html"
    table_columns = [
        Column(label=_("Reserva")),
        Column(label=_("Cliente")),
        Column(label=_("Plazo")),
        Column(label=_("Estado")),
        Column(label=_("Detalle")),
    ]
    search_placeholder = _("Reserva, cliente o lote...")
    empty_title = _("Sin partes")
    empty_message = _("Se preparan solos al entregar cada coche.")
    page_title = _("SES.Hospedajes")

    def get_base_queryset(self):
        return SesSubmission.objects.for_user(self.request.user).select_related(
            "reservation__customer"
        )


class SesScopedMixin(CrudPermissionMixin):
    def get_submission(self) -> SesSubmission:
        return get_object_or_404(
            SesSubmission.objects.for_user(self.request.user).select_related(
                "reservation__customer"
            ),
            pk=self.kwargs["pk"],
        )


class SesDetailView(SesScopedMixin, TemplateView):
    permission_required = "compliance.view_sessubmission"
    template_name = "compliance/ses_detail.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        parte = self.get_submission()
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            {
                "parte": parte,
                "envio_real": settings.SES_ENABLED,
                "page_title": str(parte),
                "breadcrumbs": [
                    {"label": _("Inicio"), "url": reverse("core:home")},
                    {"label": _("SES.Hospedajes"), "url": reverse("compliance:ses_list")},
                    {"label": parte.reservation.number},
                ],
            }
        )
        return contexto


class SesActionView(SesScopedMixin, View):
    permission_required = "compliance.change_sessubmission"

    def post(self, request, pk, accion, *args, **kwargs):
        parte = self.get_submission()
        try:
            if accion == "revalidar":
                parte = prepare(reservation=parte.reservation, actor=request.user)
                messages.info(request, _("Datos revisados."))
            elif accion == "enviar":
                parte = send(submission=parte, actor=request.user)
                messages.success(request, str(parte.get_status_display()))
        except ServiceError as exc:
            messages.warning(request, str(exc))
        return HttpResponseRedirect(reverse("compliance:ses_detail", args=[parte.pk]))


class SesXmlView(SesScopedMixin, View):
    """El XML tal cual, para subirlo a mano en la sede si hace falta."""

    permission_required = "compliance.view_sessubmission"

    def get(self, request, pk, *args, **kwargs):
        parte = self.get_submission()
        respuesta = HttpResponse(parte.xml, content_type="application/xml; charset=utf-8")
        respuesta["Content-Disposition"] = (
            f'attachment; filename="ses-{parte.reservation.number}.xml"'
        )
        return respuesta
