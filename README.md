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
<http://localhost:8000/health/>. `make seed` crea el superusuario `admin` / `admin`
(solo con `DEBUG=True`) para entrar en <http://localhost:8000/admin/>.

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
  offices/         Office
  customers/       Customer, Driver, documentos
  fleet/           VehicleCategory, Vehicle, VehicleBlock
  pricing/         Rate, RateTier, Season, Extra, motor de cálculo
  availability/    motor de disponibilidad
  reservations/    Reservation y máquina de estados
  operations/      CheckIn, CheckOut, Damage
  billing/         Payment, InvoiceSeries, Invoice, InvoiceLine
  contracts/       generación de contrato PDF
  compliance/      SES.Hospedajes (RD 933/2021)
  auditlog/        AuditLog
  settings_app/    CompanySettings
```

Las apps están creadas y registradas en `INSTALLED_APPS`, todavía sin modelos.

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

## Producción

La imagen `runtime` arranca Gunicorn con `config.settings.prod` y sirve estáticos con Whitenoise
(`CompressedManifestStaticFilesStorage`). Antes de desplegar: `DJANGO_SECRET_KEY` propia,
`DJANGO_ALLOWED_HOSTS` explícito y `collectstatic`.
