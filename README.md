# CRM Rent a Car

Sistema de gestión para una empresa de alquiler de vehículos sin conductor en España.
Multi-oficina, uso real en mostrador.

Este repositorio contiene, por ahora, **el esqueleto técnico**: infraestructura, configuración,
calidad y CI. Sin lógica de negocio.

## Arranque en 3 comandos

```bash
cp .env.example .env
make up
make seed
```

La aplicación queda en <http://localhost:8000> y el estado del sistema en
<http://localhost:8000/health/>. `make seed` crea los roles del sistema, tres oficinas de
ejemplo y el superusuario `admin@localhost` / `admin` (solo con `DEBUG=True`).

El acceso es privado: sin sesión, cualquier URL lleva a <http://localhost:8000/entrar/>.

Requisitos en la máquina: Docker y Docker Compose v2. Nada más: Python, Postgres y Node
viven dentro de los contenedores.

Si ya tienes algo escuchando en 8000 o 5432, cambia `WEB_PORT` o `POSTGRES_PORT` en tu `.env`:
solo afectan a los puertos publicados en el host, no a la red interna de compose.

## Comandos

`make help` lista todo. Los más usados:

| Comando | Qué hace |
| --- | --- |
| `make up` | Levanta web, db, redis, worker y el watcher de Tailwind |
| `make down` | Para los contenedores conservando los datos |
| `make logs` | Sigue los logs de todos los servicios |
| `make test` | pytest contra Postgres real (`ARGS="-k health"` para filtrar) |
| `make coverage` | Tests con informe de cobertura |
| `make lint` | `ruff check` + `ruff format --check` |
| `make format` | Aplica formato y arreglos automáticos |
| `make migrate` / `make makemigrations` | Migraciones |
| `make shell` / `make bash` / `make dbshell` | Consolas de Django, del contenedor y psql |
| `make clean` | Para todo y **borra la base de datos** |

## Servicios

| Servicio | Imagen | Para qué |
| --- | --- | --- |
| `web` | build local | Django (runserver en dev, Gunicorn en la imagen de prod) |
| `db` | `postgres:16` | Base de datos |
| `redis` | `redis:7-alpine` | Caché y broker de Celery |
| `worker` | build local | Celery: PDFs, envíos a SES.Hospedajes, correo |
| `tailwind` | `node:22` | Watcher de CSS y vendorizado de HTMX y Alpine (perfil `dev`) |

## Healthcheck

`GET /health/` comprueba Postgres y Redis. Devuelve `200` con `{"status": "ok"}` o `503`
con el detalle del fallo por dependencia. Lo usan el healthcheck del contenedor `web` y
`make up` para esperar a que la app esté servida.

## Estructura

```
config/            settings (base/dev/prod), urls, celery, wsgi/asgi
assets/css/        fuente de Tailwind (input.css); la salida va a static/css/app.css
static/            estáticos servidos (CSS compilado, HTMX y Alpine vendorizados)
apps/
  core/            base, mixins, utils, healthcheck, plantillas base
  accounts/        User, Role, permisos, scope de oficina
  offices/         Office, OfficePool (agrupación de oficinas para el one-way)
  customers/       Customer, documentos privados, validacion DNI/NIE
  fleet/           VehicleCategory, Vehicle, VehicleBlock
  pricing/         Season, Rate, RateTier, Extra, Supplement, Discount + motor de cálculo
  availability/    motor de disponibilidad
  reservations/    Reservation y máquina de estados
  operations/      CheckIn, CheckOut, Damage
  billing/         Payment, InvoiceSeries, Invoice, InvoiceLine
  contracts/       generación de contrato PDF
  compliance/      SES.Hospedajes (RD 933/2021)
  auditlog/        AuditLog
  settings_app/    CompanySettings
```

Con modelos y pantallas: `accounts`, `offices`, `fleet` (categorías, vehículos y bloqueos),
`customers` y `pricing` (tarifas, tramos, temporadas, extras, suplementos y descuentos).
El resto están creadas y registradas en `INSTALLED_APPS`, todavía sin modelos.

## Base de datos

PostgreSQL 16, **también en tests**. Nada de SQLite en ningún entorno: el dominio se apoya en
`tstzrange`, `btree_gist` y constraints de exclusión para impedir el doble uso de un vehículo,
y eso no existe en SQLite.

La extensión `btree_gist` se habilita en `apps/core/migrations/0001_btree_gist.py`. Comprobación:

```bash
make dbshell
\dx
```

## Configuración

Toda la configuración entra por variables de entorno vía `django-environ`. `.env.example`
documenta cada variable; `config/settings/base.py` no tiene valores de producción escritos a mano.

- `config.settings.dev` — desarrollo y tests
- `config.settings.prod` — Gunicorn, Whitenoise con manifest, HSTS, cookies seguras

## Tests

```bash
make test
make test ARGS="-k health"
make coverage
```

`pytest-django` con `--reuse-db`. Las pruebas de concurrencia usarán transacciones reales
(`django_db(transaction=True)`), por eso la base de datos es Postgres de verdad.

## CI

`.github/workflows/ci.yml` corre en cada push y pull request:

1. `ruff check` y `ruff format --check`
2. `python manage.py check`
3. `makemigrations --check --dry-run` (modelos y migraciones sincronizados)
4. `pytest` con cobertura, contra servicios Postgres 16 y Redis 7

## Frontend

Sin SPA: plantillas de Django con HTMX y Alpine. El servicio `tailwind` compila
`assets/css/input.css` a `static/css/app.css` y copia HTMX y Alpine desde `node_modules`
a `static/js/` — nada se sirve desde un CDN. La imagen de producción hace ese mismo build
en una etapa de Node y ejecuta `collectstatic` durante el build, de modo que arranca ya servible.

## Sistema de interfaz

Plantillas de Django con HTMX y Alpine; sin SPA. Todo lo transversal vive en `apps/core`.

**Catálogo vivo en [`/ui-kit/`](http://localhost:8000/ui-kit/)**: tabla, badges, modal, select con
búsqueda, fechas, confirmación destructiva, avisos y páginas de error, funcionando de verdad.
Es la referencia mientras se construyen las pantallas reales; se borra cuando sobren.

### Estructura del shell

`base.html` monta barra superior, navegación lateral, selector de oficina activa, migas de pan,
zona de avisos y el hueco de modales. Una pantalla solo rellena bloques:

```django
{% extends "base.html" %}
{% block content %}...{% endblock %}
```

Contexto que reconoce: `page_title`, `page_subtitle`, `breadcrumbs`
(`[{"label": ..., "url": ...}]`) y los bloques `page_actions` y `extra_js`.

La navegación lateral se declara en `apps/core/navigation.py`: cada app añade su `NavSection`.

### Componentes

| Partial | Para qué |
| --- | --- |
| `ui/_table.html` | Tabla con búsqueda, filtros y paginación por HTMX. La vista arma un `core.tables.Table` |
| `ui/_modal.html` | Modal genérico. Las plantillas de modal lo **extienden**, no lo incluyen |
| `ui/_confirm_dialog.html` | Confirmación de acciones destructivas. Sustituye al `window.confirm` de `hx-confirm` |
| `ui/_select_search.html` | Select con autocompletado en servidor, navegable con flechas |
| `ui/_field.html` | Renderiza un `BoundField` con etiqueta, ayuda y errores |
| `ui/_date_input.html`, `ui/_datetime_input.html` | Fecha y fecha-hora fuera de un formulario |
| `ui/_badge.html` | Badge de estado. El color por estado se registra en `core.badges` |
| `ui/_empty.html`, `ui/_pagination.html` | Estado vacío y paginación |

Las tablas usan [django-template-partials](https://github.com/carltongibson/django-template-partials):
la misma URL sirve la página entera o solo el fragmento `#resultados` según llegue o no la
cabecera `HX-Request`, así que teclear en el buscador no vuelve a traerse el shell.

### Avisos y errores

Un aviso llega por tres caminos, todos al mismo sitio:

- `django.contrib.messages` en una carga normal de página
- `core.htmx.trigger_toast(response, "...", "success")` desde una vista (cabecera `HX-Trigger`)
- un fallo de HTMX (`403`, `500`, red caída, timeout), que `static/js/app.js` convierte en aviso

`403.html`, `404.html` y `500.html` son propias. La de 500 no extiende `base.html`: cuando Django
sirve un 500 no ejecuta los procesadores de contexto.

### Oficina activa

El selector de la barra superior escribe en la sesión y **el backend valida** que la oficina esté
entre las permitidas: un POST manipulado recibe un 403. De dónde sale la lista lo decide
`CORE_OFFICE_PROVIDER`; hoy apunta a la sesión porque el modelo `Office` aún no existe.

### Modelos base

`apps/core/models.py` aporta los mixins de los que colgará todo el dominio:

| Mixin | Qué añade |
| --- | --- |
| `TimeStampedModel` | `created_at`, `updated_at` |
| `ActivableModel` | `is_active` y `.objects.active()` / `.inactive()`, con `deactivate()` en vez de borrar |
| `UserStampedModel` | `created_by` y `updated_by`, rellenados solos |

`UserStampedModel` toma el usuario de un `ContextVar` que publica
`core.middleware.CurrentUserMiddleware`, no de la request: así funciona igual en vistas, comandos
y tareas de Celery (`with current_user(usuario): ...`). El contextvar se resetea al terminar cada
petición, y hay un test con hilos concurrentes que lo comprueba.

## Maestros y patrón CRUD

Oficinas, grupos de oficinas y categorías de vehículo son las primeras pantallas de
mantenimiento, y fijan el patrón que sigue el resto: listado con `django-filter`, formulario en
modal por HTMX, validación en servidor (**422** con los errores) y baja lógica. Está documentado
en [`docs/patrones/crud.md`](docs/patrones/crud.md) y las piezas comunes viven en
`apps/core/crud.py`.

Maestros disponibles: **oficinas**, **grupos de oficinas**, **categorías**, **vehículos**,
**bloqueos de vehículo**, **clientes**, **extras**, **tarifas** (con sus tramos),
**temporadas**, **suplementos** y **descuentos**.

Ningún maestro se borra: se desactiva. No hay botón de borrar, no hay ruta de borrado y
`ActivableModel.delete()` lanza `PhysicalDeleteNotAllowed` si alguien lo intenta desde código.
Una categoría retirada deja de ofrecerse en reservas nuevas
(`fleet.selectors.selectable_categories()`) pero sigue leyéndose en el histórico y en el listado.

### Detalles que no se ven en la pantalla

- **Bloqueos de vehículo**: una `ExclusionConstraint` sobre `tstzrange` impide en la propia base
  de datos que dos bloqueos del mismo coche se solapen. El formulario avisa antes con un mensaje
  legible, pero contra dos usuarios guardando a la vez el único que llega a tiempo es Postgres.
  Es el único borrado físico del sistema: un bloqueo anulado tiene que dejar de ocupar hueco.
- **Estado del vehículo**: es derivado de la operativa. `ALQUILADO` lo pone el check-in
  (`fleet.services.start_rental`) y lo quita el check-out (`finish_rental`); desde la ficha solo
  se ponen los estados de `MANUAL_STATUSES`, y nunca sobre un coche que está fuera.
- **Documento del cliente**: DNI y NIE se validan con su letra de control; el pasaporte no se
  valida por patrón, porque cada país tiene el suyo y rechazarlo sería rechazar clientes reales.
- **Búsqueda de clientes**: columna `search_text` (minúsculas y sin acentos) con índice GIN
  trigram. "gonzal" encuentra "González" en menos de 100 ms con 50.000 clientes; hay un test de
  volumen que lo comprueba (`make test ARGS="-m slow"`).
- **Documentos escaneados**: fuera de `MEDIA_ROOT`, en un almacén sin URL. Se descargan por una
  vista que comprueba permisos.

## Motor de tarifas

Un único sitio donde se calcula un precio: `pricing.services.calculate_reservation_price()`.
Recibe un `PriceQuoteInput` y devuelve un `PriceBreakdown` con el desglose línea a línea, listo
para pintar en la ficha de reserva o para volcar a factura. No conoce `Reservation`, así que se
puede pedir un presupuesto antes de que la reserva exista.

El administrador lo configura entero desde la aplicación: tarifas con editor de tramos,
temporadas, suplementos, descuentos, un **simulador** que calcula con la configuración real y una
pantalla de **conflictos** que avisa de dos tarifas que se pisan.

Documentado en [`docs/motor-tarifas.md`](docs/motor-tarifas.md), incluido lo que más sorprende:

- `rental_days()` es la **única** función que convierte fechas en días facturables (24 h + margen
  de cortesía configurable).
- Una reserva que cruza temporada se cobra entera a la de la **fecha de recogida**, y lo avisa.
- Tramos en modo **PLANO** (por defecto): 7 días = 280 € y 8 días = 280 €. Alargar puede abaratar,
  es intencionado y hay un test que lo fija para que nadie lo "arregle". El modo **PROGRESIVO**
  está implementado para quien lo quiera.
- Sin tarifa aplicable → `NoRateAvailable`. Con dos tarifas igual de aplicables → `AmbiguousRate`.
  Nunca un precio 0 ni una elección silenciosa.
- `Decimal` y `ROUND_HALF_UP`, redondeando al cerrar cada línea. Un test de propiedad con 1.000
  combinaciones aleatorias comprueba que base + IVA cuadra con el total al céntimo.

## Acceso y permisos

Sistema privado. `LoginRequiredMiddleware` obliga a tener sesión en **todo**, y lo público se
marca una a una con `@login_not_required` (login, recuperación de contraseña y `/health/`).
No hay alta pública: `/registro/`, `/signup/` y compañía responden **410 Gone** a propósito,
para que quede escrito que no es un olvido.

### Usuario y roles

Se entra con **correo y contraseña**, no con nombre de usuario. Un usuario tiene un `Role`
(conjunto de permisos con nombre) y un conjunto de **oficinas**.

```
permisos efectivos = los del rol + los asignados a mano + los del grupo
```

Los del rol los aporta `accounts.backends.RolePermissionsBackend`. Los roles del sistema se
declaran en `apps/accounts/roles.py` y se aplican con:

```bash
make manage ARGS="sync_roles"          # añade lo que falte
make manage ARGS="sync_roles --prune"  # además quita lo que sobra
```

El comando avisa de los permisos que aún no existen porque su app no tiene modelos todavía, y
los reparte solo en cuanto existan. Es idempotente.

### Aislamiento por oficina

Regla del sistema: **toda consulta de datos operativos pasa por el scope.**

```python
class Reservation(OfficeScopedModel):   # apps/accounts/scoping.py
    ...

Reservation.objects.for_user(request.user)   # superuser: todo; el resto: sus oficinas
```

En las vistas, `OfficeScopedMixin` recorta el queryset. Un objeto de otra oficina da **404, no
403**: un 403 confirmaría que ese registro existe, y eso ya es información que el usuario no
debería tener.

En los formularios, `OfficeScopedFormMixin` recorta el queryset del campo de oficina, de modo
que un POST con el id de otra oficina **falla la validación**. El selector de oficina activa de
la barra superior guarda en sesión, pero siempre contrasta contra las oficinas del usuario: un
`office_id` manipulado recibe un 403 y la sesión no cambia.

### Permisos personalizados

Además de los de Django (`add_*`, `change_*`, `view_*`, `delete_*`):

| Permiso | Dónde |
| --- | --- |
| `accounts.manage_users` | `accounts.User` |
| `settings_app.access_settings`, `pricing.manage_rates` | ancla de su app |
| `billing.view_billing`, `billing.add_payment` | ancla de su app |
| `reservations.change_reservation_price`, `cancel_reservation`, `delete_reservation` | ancla de su app |
| `availability.override_availability` | ancla de su app |

Las apps de dominio todavía no tienen modelos, así que sus permisos cuelgan de un modelo ancla
`managed = False` que **no crea tabla**: solo aporta el content type de la app, para que las
comprobaciones se escriban desde el principio como `reservations.cancel_reservation` y no haya
que reescribirlas después.

### Bloqueo por intentos fallidos

`django-axes`, por combinación de IP + usuario (bloquear solo por usuario permitiría dejar fuera
a un compañero de mostrador a base de fallar su correo). Configurable con `AXES_FAILURE_LIMIT`
y `AXES_COOLOFF_MINUTES`. Al superar el límite la respuesta es **429**. Cada entrada, salida,
fallo y bloqueo se registra con structlog, y axes guarda además los intentos en su tabla.

### Panel de usuarios

En `/usuarios/`, con permiso `accounts.manage_users`: listar, buscar, crear, editar, desactivar
y reactivar. Un usuario **nunca se borra** (rompería la auditoría) y al crearlo no se le asigna
una contraseña que haya que dictarle por teléfono: la estrena él con el enlace de recuperación.

El admin de Django queda en `/admin-interno/` como herramienta de soporte técnico, solo para
`is_staff`, y con el borrado de usuarios y oficinas desactivado.

## Producción

La imagen `runtime` arranca Gunicorn con `config.settings.prod` y sirve estáticos con Whitenoise
(`CompressedManifestStaticFilesStorage`). Antes de desplegar: `DJANGO_SECRET_KEY` propia,
`DJANGO_ALLOWED_HOSTS` explícito y `collectstatic`.
