"""Patron CRUD del proyecto.

Listado con django-filter, formulario en modal por HTMX, validacion en
servidor, avisos y permisos. Documentado en `docs/patrones/crud.md`.

Las vistas de aqui orquestan: ni calculan ni contienen reglas de negocio. Lo
que haya que decidir vive en el servicio o en el modelo.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, ListView, UpdateView, View

from .htmx import trigger_event, trigger_toast
from .models import ActivableModel
from .tables import Table, paginate

#: Evento que dispara el servidor tras guardar. El listado lo escucha y se
#: recarga solo, sin que la vista del formulario sepa quien lo pinta.
EVENTO_GUARDADO = "crud:guardado"


class CrudPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Sesion y permiso. 403 en vez de redirigir al login en bucle."""

    raise_exception = True


class CrudListView(CrudPermissionMixin, ListView):
    """Listado filtrable y paginado que se refresca sin recargar la pagina."""

    #: FilterSet de django-filter. Es quien define busqueda y filtros.
    filterset_class = None
    #: Id del contenedor que HTMX reemplaza. Unico en la pagina.
    table_id = ""
    #: Columnas (core.tables.Column) y plantilla de fila.
    table_columns: list = []
    table_row_template = ""
    search_placeholder = ""
    empty_title = ""
    empty_message = ""
    paginate_by = 25
    #: True cuando el modelo tiene `for_user()` y hay que recortar por oficina.
    scope_to_user = False

    def get_base_queryset(self):
        consulta = self.model._default_manager.all()
        if self.scope_to_user:
            consulta = consulta.for_user(self.request.user)
        return consulta

    def get_queryset(self):
        self.filterset = self.filterset_class(
            self.request.GET or None,
            queryset=self.get_base_queryset(),
            request=self.request,
        )
        return self.filterset.qs

    def get_list_url(self) -> str:
        return self.request.path

    def get_table(self, queryset) -> Table:
        return Table(
            id=self.table_id,
            url=self.get_list_url(),
            columns=list(self.table_columns),
            page_obj=paginate(self.request, queryset, self.paginate_by),
            row_template=self.table_row_template,
            search_value=self.request.GET.get("q", ""),
            search_placeholder=self.search_placeholder,
            filter_form=self.filterset.form,
            refresh_event=EVENTO_GUARDADO,
            empty_title=self.empty_title,
            empty_message=self.empty_message,
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["table"] = self.get_table(contexto["object_list"])
        return contexto

    def render_to_response(self, context, **kwargs):
        # Con HTMX solo viaja la tabla; el resto de la pagina ya esta puesto.
        if self.request.htmx:
            return render(self.request, "ui/_table.html#resultados", context)
        return super().render_to_response(context, **kwargs)


class ModalFormMixin(CrudPermissionMixin):
    """Formulario servido dentro del modal generico.

    Al guardar no se devuelve una redireccion: se devuelve un cuerpo vacio, que
    cierra el modal, y dos eventos en la cabecera HX-Trigger, uno para refrescar
    el listado y otro para el aviso. Asi el formulario no necesita saber desde
    donde lo han abierto.
    """

    template_name = "ui/_modal_form.html"
    modal_title = ""
    submit_label = _("Guardar")
    success_message = ""

    def get_form_action(self) -> str:
        return self.request.path

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modal_title"] = self.modal_title
        contexto["submit_label"] = self.submit_label
        contexto["form_action"] = self.get_form_action()
        contexto["form_id"] = f"form-{self.table_id or 'modal'}"
        return contexto

    def form_invalid(self, form):
        # 422 y no 200: el navegador tiene que saber que no se ha guardado.
        # app.js manda a HTMX pintar igualmente el cuerpo, que trae los errores.
        return self.render_to_response(self.get_context_data(form=form), status=422)

    def get_success_message(self, objeto) -> str:
        return self.success_message % {"objeto": objeto} if self.success_message else ""

    def form_valid(self, form):
        self.object = self.save_object(form)
        respuesta = HttpResponse(status=200)  # cuerpo vacio: cierra el modal
        trigger_event(respuesta, EVENTO_GUARDADO)
        mensaje = self.get_success_message(self.object)
        if mensaje:
            trigger_toast(respuesta, mensaje, "success")
        return respuesta

    def save_object(self, form):
        """Punto de enganche para llamar a un servicio en vez de a form.save()."""
        return form.save()


class ModalCreateView(ModalFormMixin, CreateView):
    table_id = ""


class ModalUpdateView(ModalFormMixin, UpdateView):
    table_id = ""

    def get_base_queryset(self):
        return self.model._default_manager.all()

    def get_queryset(self):
        return self.get_base_queryset()


class ToggleActiveView(CrudPermissionMixin, View):
    """Activa o desactiva un maestro. Nunca borra."""

    model = None
    #: True activa, False desactiva.
    activate = True
    activated_message = _("%(objeto)s reactivado.")
    deactivated_message = _("%(objeto)s desactivado.")

    def get_queryset(self):
        return self.model._default_manager.all()

    def post(self, request, pk, *args, **kwargs):
        objeto = get_object_or_404(self.get_queryset(), pk=pk)
        if not isinstance(objeto, ActivableModel):
            raise TypeError(f"{type(objeto).__name__} no admite activar/desactivar.")

        if self.activate:
            objeto.activate()
            mensaje = self.activated_message
        else:
            objeto.deactivate()
            mensaje = self.deactivated_message

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, mensaje % {"objeto": objeto}, "success")


def modal_url(nombre, *args) -> str:
    """Atajo para las plantillas de fila."""
    return reverse(nombre, args=args)
