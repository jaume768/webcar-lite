"""Pantallas de informes y descargas para la gestoria.

Las vistas orquestan: leen el periodo de la URL, se lo pasan a `services` o a
`exports` y pintan. Ningun calculo vive aqui.
"""

import structlog
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.forms import DateField

from . import exports, services

logger = structlog.get_logger(__name__)


class ReportAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Los informes ensenan el negocio entero: se piden permiso y sesion."""

    permission_required = "reports.view_reports"
    raise_exception = True

    def get_period(self):
        """Periodo pedido en la URL: fechas sueltas o un atajo con nombre.

        Una fecha mal escrita no revienta la pantalla: se cae al periodo por
        defecto, que es el ultimo ano.
        """
        codigo = self.request.GET.get("periodo", "ano")
        if codigo not in services.PERIODOS:
            codigo = "ano"
        desde, hasta = services.default_range(codigo)

        campo = DateField(required=False)
        for nombre in ("desde", "hasta"):
            crudo = self.request.GET.get(nombre)
            if not crudo:
                continue
            try:
                elegida = campo.clean(crudo)
            except Exception:  # fecha ilegible: se queda la del periodo
                continue
            if elegida:
                if nombre == "desde":
                    desde = elegida
                else:
                    hasta = elegida

        # Un rango al reves no dice nada: se endereza en vez de dar error.
        if hasta < desde:
            desde, hasta = hasta, desde
        return codigo, desde, hasta


class ReportsView(ReportAccessMixin, TemplateView):
    """Ingresos por coche y ocupacion por mes, en una sola pantalla."""

    template_name = "reports/reports.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        codigo, desde, hasta = self.get_period()

        ingresos = services.revenue_by_vehicle(user=self.request.user, desde=desde, hasta=hasta)
        contexto.update(
            {
                "page_title": _("Informes"),
                "breadcrumbs": [
                    {"label": _("Inicio"), "url": reverse("core:home")},
                    {"label": _("Informes")},
                ],
                "periodo": codigo,
                "periodos": services.PERIODOS,
                "desde": desde,
                "hasta": hasta,
                "ingresos": ingresos,
                "resumen": services.summarize(ingresos),
                "ocupacion": services.occupancy_by_month(
                    user=self.request.user, desde=desde, hasta=hasta
                ),
                "hoy": timezone.localdate(),
            }
        )
        return contexto


class ExportView(ReportAccessMixin, View):
    """Descarga de un CSV. `documento` decide cual."""

    #: "facturas" o "cobros".
    documento = "facturas"

    def get(self, request, *args, **kwargs):
        _codigo, desde, hasta = self.get_period()

        if self.documento == "facturas":
            contenido = exports.invoices_csv(user=request.user, desde=desde, hasta=hasta)
            nombre = exports.filename("facturas", desde, hasta)
        else:
            contenido = exports.payments_csv(user=request.user, desde=desde, hasta=hasta)
            nombre = exports.filename("cobros", desde, hasta)

        logger.info(
            "exportacion_gestoria",
            documento=self.documento,
            desde=desde.isoformat(),
            hasta=hasta.isoformat(),
            user_id=request.user.pk,
        )

        # utf-8-sig: el BOM es lo que hace que Excel respete los acentos.
        respuesta = HttpResponse(
            contenido.encode("utf-8-sig"), content_type="text/csv; charset=utf-8"
        )
        respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
        return respuesta
