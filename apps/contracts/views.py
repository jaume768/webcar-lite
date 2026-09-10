"""Emision y descarga de contratos."""

import structlog
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

from apps.core.crud import CrudPermissionMixin
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.services import ServiceError
from apps.reservations.models import Reservation

from .models import Contract
from .services import request_contract

logger = structlog.get_logger(__name__)

EVENTO_RESERVA = "reserva:actualizada"


class ReservationScopedView(CrudPermissionMixin, View):
    """Base de todo lo que cuelga de una reserva.

    El scope de oficina va en la consulta, no en un `if`: una reserva de otra
    oficina no existe para este usuario, ni escribiendo la URL a mano.
    """

    permission_required = "reservations.view_reservation"

    def get_reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related(
                    "customer", "vehicle", "category", "pickup_office", "return_office"
                ),
                pk=self.kwargs["pk"],
            )
        return self._reserva


class ContractCreateView(ReservationScopedView):
    """Pide el contrato. Devuelve enseguida: lo genera Celery."""

    permission_required = "reservations.change_reservation"

    def post(self, request, *args, **kwargs):
        try:
            request_contract(reservation=self.get_reservation(), actor=request.user)
        except ServiceError as exc:
            return trigger_toast(HttpResponse(status=200), str(exc), "warning")

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_RESERVA)
        return trigger_toast(
            respuesta, _("Contrato en camino. Se puede descargar en unos segundos."), "info"
        )


class ContractDownloadView(ReservationScopedView):
    """Entrega el PDF. Es la unica forma de leerlo: no tiene URL publica."""

    permission_required = "reservations.view_reservation"

    def get(self, request, *args, **kwargs):
        reserva = self.get_reservation()
        contrato = get_object_or_404(
            Contract.objects.select_related("reservation"),
            pk=self.kwargs["contract_pk"],
            reservation=reserva,
        )
        if not contrato.is_ready:
            return HttpResponse(
                _("El contrato todavia se esta generando."), status=409, content_type="text/plain"
            )

        logger.info(
            "contrato_descargado",
            contract_id=contrato.pk,
            reservation_number=reserva.number,
            user_id=request.user.pk,
        )
        return FileResponse(
            contrato.file.open("rb"), as_attachment=True, filename=contrato.filename
        )


class DamagePhotoDownloadView(ReservationScopedView):
    """Entrega la foto de un dano, con el mismo control que el contrato."""

    permission_required = "reservations.view_reservation"

    def get(self, request, *args, **kwargs):
        from apps.operations.models import DamagePhoto

        reserva = self.get_reservation()
        foto = get_object_or_404(
            DamagePhoto.objects.select_related("damage"),
            pk=self.kwargs["photo_pk"],
            damage__reservation=reserva,
        )
        logger.info(
            "foto_de_dano_descargada",
            photo_id=foto.pk,
            reservation_number=reserva.number,
            user_id=request.user.pk,
        )
        return FileResponse(
            foto.image.open("rb"),
            as_attachment=True,
            filename=foto.image.name.rsplit("/", 1)[-1],
        )


class DocumentsPanelView(ReservationScopedView):
    """Panel de documentos suelto. Lo repide HTMX mientras algo se genera."""

    permission_required = "reservations.view_reservation"

    def get(self, request, *args, **kwargs):
        from .selectors import documents_for

        reserva = self.get_reservation()
        documentos = documents_for(reserva)
        return render(
            request,
            "contracts/_documents_panel.html",
            {
                "reservation": reserva,
                "documentos": documentos,
                "generando": any(doc.status == "pending" for doc in documentos),
            },
        )
