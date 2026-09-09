# Patron CRUD

Este es el patron con el que se construyen **todas** las pantallas de maestros
del proyecto. Oficinas (`apps/offices`) y categorias de vehiculo (`apps/fleet`)
son la referencia: cualquier maestro nuevo (vehiculos, clientes, extras,
tarifas) se hace copiando esta estructura, no inventando otra.

Las piezas comunes viven en `apps/core`:

| Fichero | Que aporta |
| --- | --- |
| `core/crud.py` | `CrudListView`, `ModalCreateView`, `ModalUpdateView`, `ModalFormView`, `ToggleActiveView` |
| `core/tables.py` | `Table` y `Column`: la configuracion que pinta `ui/_table.html` |
| `core/filters.py` | `EstadoFilterMixin`: el filtro activo/desactivado |
| `core/services.py` | `ServiceError`: "no se puede, y por esto" |
| `core/storage.py` | `PrivateFileSystemStorage`: ficheros sin URL publica |
| `core/templates/core/crud_list.html` | pagina de listado con boton de alta |
| `core/templates/ui/_modal_form.html` | formulario dentro del modal |
| `core/templates/ui/_form_page.html` | el mismo formulario como pagina, sin HTMX |
| `core/templates/ui/_form_body.html` | el `<form>` que comparten los dos |

## Ficheros de una app con CRUD

```
apps/<app>/
  models.py       modelo (TimeStampedModel + ActivableModel)
  services.py     reglas de negocio: crear, actualizar, activar, desactivar
  selectors.py    consultas con nombre (que se puede vender hoy, que ve el usuario)
  filters.py      FilterSet de django-filter: busqueda y desplegables
  forms.py        ModelForm: valida y normaliza, no guarda reglas
  views.py        orquesta: no calcula
  urls.py         listado, alta, edicion, activar, desactivar
  templates/<app>/_<algo>_row.html   las celdas de una fila
```

## El flujo, de arriba a abajo

1. **Listado** (`CrudListView`). Declara columnas, plantilla de fila, filtro y
   textos. El queryset base sale del modelo; si el modelo tiene oficina, se
   pone `scope_to_user = True` y se recorta con `for_user()`.
2. **Busqueda y filtros**: los define el `FilterSet`, nunca la vista. El campo
   `q` lo pinta el buscador de la cabecera; los demas salen como desplegables.
   Cualquier cambio recarga solo la tabla (`hx-get` a la misma URL, que
   devuelve el fragmento `ui/_table.html#resultados`).
3. **Alta y edicion**: `ModalCreateView` / `ModalUpdateView`. El boton hace
   `hx-get` contra `#modal-host` y el servidor devuelve `ui/_modal_form.html`.
   Si la peticion no viene de HTMX (URL directa, o navegador sin JavaScript),
   la misma vista sirve `ui/_form_page.html` y termina redirigiendo al listado.
4. **Validacion**: siempre en servidor. Un formulario invalido responde **422**
   con el formulario y sus errores; `static/js/app.js` le dice a HTMX que pinte
   igualmente ese cuerpo, porque es justo lo que el usuario tiene que leer.
5. **Guardar**: la vista no llama a `form.save()`. Sobreescribe `save_object()`
   y llama al servicio de la app, que es quien normaliza, registra y decide.
6. **Si el servicio se niega** (`ServiceError`): el motivo se pinta como error
   del formulario y la respuesta sigue siendo un 422. Un solape de bloqueos que
   el formulario no llego a ver (porque otro usuario guardo un segundo antes) se
   lee igual de claro que un campo mal rellenado, y nunca es un 500.
7. **Al guardar bien**: respuesta 200 **con el cuerpo vacio** (eso cierra el
   modal) y dos eventos en `HX-Trigger`:
   - `crud:guardado`, que el listado escucha para recargarse solo, conservando
     busqueda, filtros y pagina;
   - `toast`, el aviso que ve el usuario.
8. **Activar / desactivar** (`ToggleActiveView`): un `hx-post` con
   `hx-swap="none"`. Si el servicio se niega (`ServiceError`), el usuario recibe
   el motivo como aviso y la tabla no se refresca, porque no ha cambiado nada.

## Acciones que no son un alta ni una edicion

`ModalFormView` sirve un formulario suelto en el mismo modal: cambiar la
situacion de un vehiculo (`fleet:vehicle_status`) o marcar a un cliente como
conflictivo (`customers:customer_blacklist`). El formulario recoge y valida;
`save_object()` llama al servicio, que es quien decide si el cambio tiene
sentido. Es la forma de que un estado derivado de la operativa no se pueda
falsear desde una pantalla.

## Ficheros privados

Los documentos de cliente (DNI, carnet) no se guardan en `MEDIA_ROOT`: van al
almacen `private`, que es `core.storage.PrivateFileSystemStorage` y **no tiene
URL** (pedirsela lanza `ValueError` a proposito). Se entregan por una vista que
comprueba permisos, `customers:document_download`. Cualquier otro fichero con
datos personales sigue el mismo camino.

## Reglas que no se negocian

- **Nada de borrado fisico.** No hay vista de borrado ni boton en la interfaz, y
  `ActivableModel.delete()` lanza `PhysicalDeleteNotAllowed` si alguien lo
  intenta desde codigo. Un maestro dado de baja sigue apareciendo en las
  reservas y facturas historicas.
- **Un maestro desactivado desaparece de los selectores de venta, no del
  historico ni del listado.** El listado lo sigue enseniando (y el filtro de
  estado lo separa) porque desde ahi se vuelve a poner en catalogo. Quien
  decide que se puede vender hoy es el selector del dominio, por ejemplo
  `fleet.selectors.selectable_categories()`.
- **Los permisos se validan en la vista, siempre.** `permission_required` en
  todas: listado, alta, edicion y cada toggle. Esconder el boton
  (`create_permission`, `{% if perms.app.change_x %}`) es cortesia, no
  seguridad.
- **El scope de oficina se aplica en el queryset**, no en la plantilla. Con
  `scope_to_user = True` la vista recorta a las oficinas del usuario, tanto al
  leer como al editar por URL directa.
- **Un borrado fisico hay que justificarlo.** Solo hay uno en todo el sistema:
  `VehicleBlock`, porque es un apunte de agenda y mientras la fila exista sigue
  ocupando hueco en la constraint de exclusion. Todo lo demas se desactiva.
- **La logica vive en `services.py`.** Las vistas orquestan. Si aparece un
  calculo o una regla dentro de una vista, esta en el sitio equivocado.

## Esqueleto minimo

```python
class ExtraListView(CrudListView):
    permission_required = "pricing.view_extra"
    model = Extra
    filterset_class = ExtraFilter
    table_id = "tabla-extras"
    table_row_template = "pricing/_extra_row.html"
    table_columns = [Column(label=_("Codigo")), Column(label=_("Nombre"))]
    page_title = _("Extras")
    create_url_name = "pricing:extra_create"
    create_permission = "pricing.add_extra"


class ExtraCreateView(ModalCreateView):
    permission_required = "pricing.add_extra"
    model = Extra
    form_class = ExtraForm
    table_id = "tabla-extras"
    list_url_name = "pricing:extra_list"
    modal_title = _("Nuevo extra")
    success_message = _("Extra %(objeto)s creado.")

    def save_object(self, form):
        return save_extra(extra=form.save(commit=False), actor=self.request.user)
```
