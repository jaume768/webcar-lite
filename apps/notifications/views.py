"""Registro de correos al cliente: que salio, a quien y si fallo."""

import django_filters as filters
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views import View

from apps.core.crud import EVENTO_GUARDADO, CrudListView, CrudPermissionMixin
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.tables import Column

from .models import EmailKind, EmailLog, EmailStatus


class EmailLogFilter(filters.FilterSet):
    q = filters.CharFilter(method="buscar", label=_("Buscar"))
    kind = filters.ChoiceFilter(
        label=_("Tipo"), choices=EmailKind.choices, empty_label=_("Tipo: todos")
    )
    status = filters.ChoiceFilter(
        label=_("Estado"), choices=EmailStatus.choices, empty_label=_("Estado: todos")
    )

    class Meta:
        model = EmailLog
        fields = ["q", "kind", "status"]

    def buscar(self, queryset, name, value):
        termino = (value or "").strip()
        if not termino:
            return queryset
        return queryset.filter(
            Q(to_email__icontains=termino)
            | Q(subject__icontains=termino)
            | Q(reservation__number__icontains=termino)
        )


class EmailLogListView(CrudListView):
    permission_required = "notifications.view_emaillog"
    model = EmailLog
    filterset_class = EmailLogFilter
    table_id = "tabla-correos"
    table_row_template = "notifications/_email_row.html"
    table_columns = [
        Column(label=_("Cuándo")),
        Column(label=_("Tipo")),
        Column(label=_("Para")),
        Column(label=_("Reserva")),
        Column(label=_("Idioma")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Correo, asunto o reserva...")
    empty_title = _("Sin correos")
    empty_message = _("Aquí sale cada correo automático que se envía al cliente.")
    page_title = _("Correos al cliente")

    def get_base_queryset(self):
        return EmailLog.objects.for_user(self.request.user).select_related("reservation")


class EmailRetryView(CrudPermissionMixin, View):
    """Vuelve a intentar un correo que fallo (p. ej. con la clave de Brevo mal)."""

    permission_required = "notifications.view_emaillog"

    def post(self, request, pk, *args, **kwargs):
        from .tasks import send_email_task

        apunte = get_object_or_404(EmailLog.objects.for_user(request.user), pk=pk)
        if apunte.status != EmailStatus.FAILED or not apunte.to_email:
            return trigger_toast(
                HttpResponse(status=200), _("Ese correo no se puede reintentar."), "warning"
            )
        EmailLog.objects.filter(pk=apunte.pk).update(status=EmailStatus.QUEUED)
        send_email_task.delay(apunte.pk)
        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, _("Correo en cola de nuevo."), "success")
