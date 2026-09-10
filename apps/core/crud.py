"""Patron CRUD del proyecto.

Listado con django-filter, formulario en modal por HTMX, validacion en
servidor, avisos y permisos. Documentado en `docs/patrones/crud.md`.

Las vistas de aqui orquestan: ni calculan ni contienen reglas de negocio. Lo
que haya que decidir vive en el servicio o en el modelo.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, FormView, ListView, UpdateView, View

from .htmx import trigger_event, trigger_toast
from .models import ActivableModel
from .services import ServiceError
from .tables import Table, paginate

#: Evento que dispara el servidor tras guardar. El listado lo escucha y se
#: recarga solo, sin que la vista del formulario sepa quien lo pinta.
EVENTO_GUARDADO = "crud:guardado"

#: Nivel de aviso equivalente en django.contrib.messages, para las respuestas
#: que no vienen de HTMX y acaban en una redireccion.
NIVELES_MENSAJE = {
    "success": messages.SUCCESS,
    "info": messages.INFO,
    "warning": messages.WARNING,
    "error": messages.ERROR,
}


class CrudPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Sesion y permiso. 403 en vez de redirigir al login en bucle."""

    raise_exception = True


class CrudListView(CrudPermissionMixin, ListView):
    """Listado filtrable y paginado que se refresca sin recargar la pagina."""

    template_name = "core/crud_list.html"
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
    #: Alta: nombre de la ruta, etiqueta del boton y permiso que lo destapa.
    create_url_name = ""
    create_label = _("Nuevo")
    #: False cuando el alta es una pantalla entera y no un modal.
    create_in_modal = True
    create_permission = ""
    page_title = ""
    #: Ultima miga de pan. Vacia: se usa `page_title`.
    breadcrumb_label = ""

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

    def get_create_url(self) -> str:
        """URL del alta, o cadena vacia si el usuario no puede dar de alta.

        Esconder el boton no es la validacion: la vista de alta exige su propio
        permiso. Esto solo evita ensenar una puerta cerrada.
        """
        if not self.create_url_name:
            return ""
        if self.create_permission and not self.request.user.has_perm(self.create_permission):
            return ""
        return reverse(self.create_url_name)

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
            refresh_url=self.request.get_full_path(),
            empty_title=self.empty_title,
            empty_message=self.empty_message,
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["table"] = self.get_table(contexto["object_list"])
        contexto["page_title"] = self.page_title
        contexto["create_url"] = self.get_create_url()
        contexto["create_label"] = self.create_label
        contexto["create_in_modal"] = self.create_in_modal
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": self.breadcrumb_label or self.page_title},
        ]
        return contexto

    def render_to_response(self, context, **kwargs):
        # Con HTMX solo viaja la tabla; el resto de la pagina ya esta puesto.
        if self.request.htmx:
            return render(self.request, "ui/_table.html#resultados", context)
        return super().render_to_response(context, **kwargs)


class ModalFormMixin(CrudPermissionMixin):
    """Formulario servido dentro del modal generico.

    Al guardar por HTMX no se devuelve una redireccion: se devuelve un cuerpo
    vacio, que cierra el modal, y dos eventos en la cabecera HX-Trigger, uno
    para refrescar el listado y otro para el aviso. Asi el formulario no
    necesita saber desde donde lo han abierto.

    Sin HTMX (entrar por la URL directa, o navegar sin JavaScript) la misma
    vista sirve el formulario como pagina completa y acaba redirigiendo al
    listado: la pantalla no puede quedar en blanco.
    """

    modal_title = ""
    submit_label = _("Guardar")
    success_message = ""
    #: Nombre de la ruta del listado al que se vuelve sin HTMX.
    list_url_name = ""
    #: Id de la tabla del listado. Da nombre al formulario dentro de la pagina.
    table_id = ""
    #: Ancho del modal (clase de Tailwind). Un formulario con hijos pide mas.
    modal_width = "max-w-lg"
    #: Formset de hijos que se guarda con el padre (los tramos de una tarifa) y
    #: la plantilla que lo pinta. Van juntos: guardar solo el padre dejaria la
    #: tarifa a medias.
    formset_class = None
    formset_template = ""

    def get_template_names(self) -> list[str]:
        return ["ui/_modal_form.html"] if self.request.htmx else ["ui/_form_page.html"]

    def build_formset(self, *, data=None, instance=None):
        if self.formset_class is None:
            return None
        return self.formset_class(data=data, instance=instance)

    def get_formset(self):
        """El formset de la peticion, ya validado si venia en el POST."""
        if getattr(self, "_formset", None) is None:
            self._formset = self.build_formset(instance=getattr(self, "object", None))
        return self._formset

    def get_form_action(self) -> str:
        return self.request.path

    def get_list_url(self) -> str:
        return reverse(self.list_url_name)

    def get_success_url(self) -> str:
        return self.get_list_url()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modal_title"] = self.modal_title
        contexto["page_title"] = self.modal_title
        contexto["submit_label"] = self.submit_label
        contexto["form_action"] = self.get_form_action()
        contexto["form_id"] = f"form-{self.table_id or 'modal'}"
        contexto["cancel_url"] = self.get_list_url()
        contexto["modal_width"] = self.modal_width
        contexto["formset"] = self.get_formset()
        contexto["formset_template"] = self.formset_template
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": self.modal_title},
        ]
        return contexto

    def form_invalid(self, form):
        # 422 y no 200: el navegador tiene que saber que no se ha guardado.
        # app.js manda a HTMX pintar igualmente el cuerpo, que trae los errores.
        estado = 422 if self.request.htmx else 200
        return self.render_to_response(self.get_context_data(form=form), status=estado)

    def get_success_message(self, objeto) -> str:
        return self.success_message % {"objeto": objeto} if self.success_message else ""

    def form_valid(self, form):
        if self.formset_class is not None:
            # Los hijos se validan **antes** de tocar nada: unos tramos con un
            # hueco no pueden guardarse ni aunque la tarifa este perfecta.
            self._formset = self.build_formset(data=self.request.POST, instance=form.instance)
            if not self._formset.is_valid():
                return self.form_invalid(form)

        try:
            self.object = self.save_object(form)
            # `save(commit=False)` deja los M2M sin escribir, y el servicio no
            # tiene por que saberlo. Se guardan aqui, con la fila ya creada:
            # sin esto, una tarifa se guardaria sin sus categorias y no se
            # aplicaria nunca.
            guardar_relaciones = getattr(form, "save_m2m", None)
            if guardar_relaciones is not None:
                guardar_relaciones()
        except ServiceError as exc:
            # El servicio se ha negado (un solape que el formulario no vio, una
            # regla de dominio). Es un error del formulario, no un 500: se
            # devuelve el mismo formulario con el motivo escrito arriba.
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        mensaje = self.get_success_message(self.object)

        if not self.request.htmx:
            if mensaje:
                messages.success(self.request, mensaje)
            return HttpResponseRedirect(self.get_success_url())

        respuesta = HttpResponse(status=200)  # cuerpo vacio: cierra el modal
        trigger_event(respuesta, EVENTO_GUARDADO)
        if mensaje:
            trigger_toast(respuesta, mensaje, "success")
        return respuesta

    def save_object(self, form):
        """Punto de enganche para llamar a un servicio en vez de a form.save()."""
        return form.save()


class ModalCreateView(ModalFormMixin, CreateView):
    pass


class ModalFormView(ModalFormMixin, FormView):
    """Modal con un formulario que no es de un modelo.

    Para acciones con datos propios (cambiar el estado de un vehiculo, por
    ejemplo): el formulario recoge y valida, y `save_object()` llama al
    servicio que hace el trabajo.
    """

    def save_object(self, form):
        raise NotImplementedError("Una accion en modal tiene que decir que hace al guardar.")


class ModalUpdateView(ModalFormMixin, UpdateView):
    def get_base_queryset(self):
        return self.model._default_manager.all()

    def get_queryset(self):
        return self.get_base_queryset()


class ToggleActiveView(CrudPermissionMixin, View):
    """Activa o desactiva un maestro. Nunca borra.

    No existe vista de borrado, y no es un olvido: los maestros dejan huella en
    reservas y facturas historicas. `ActivableModel.delete()` revienta a
    proposito si alguien lo intenta desde codigo.

    Si el servicio del dominio se niega (`ServiceError`), el usuario recibe el
    motivo como aviso y el listado no se refresca, porque no ha cambiado nada.
    """

    model = None
    #: True activa, False desactiva.
    activate = True
    activated_message = _("%(objeto)s reactivado.")
    deactivated_message = _("%(objeto)s desactivado.")
    #: Listado al que se vuelve cuando la peticion no viene de HTMX.
    list_url_name = ""

    def get_queryset(self):
        return self.model._default_manager.all()

    def perform(self, objeto):
        """Aplica el cambio. Se sobreescribe para pasar por un servicio."""
        if self.activate:
            objeto.activate()
        else:
            objeto.deactivate()

    def post(self, request, pk, *args, **kwargs):
        objeto = get_object_or_404(self.get_queryset(), pk=pk)
        if not isinstance(objeto, ActivableModel):
            raise TypeError(f"{type(objeto).__name__} no admite activar/desactivar.")

        try:
            self.perform(objeto)
        except ServiceError as exc:
            return self.responder(request, str(exc), "warning", cambiado=False)

        plantilla = self.activated_message if self.activate else self.deactivated_message
        return self.responder(request, plantilla % {"objeto": objeto}, "success", cambiado=True)

    def responder(self, request, mensaje, nivel, *, cambiado: bool):
        if not request.htmx:
            messages.add_message(request, NIVELES_MENSAJE[nivel], mensaje)
            return HttpResponseRedirect(reverse(self.list_url_name))

        respuesta = HttpResponse(status=200)
        if cambiado:
            trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, mensaje, nivel)
