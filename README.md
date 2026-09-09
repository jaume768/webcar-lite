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

## Producción

La imagen `runtime` arranca Gunicorn con `config.settings.prod` y sirve estáticos con Whitenoise
(`CompressedManifestStaticFilesStorage`). Antes de desplegar: `DJANGO_SECRET_KEY` propia,
`DJANGO_ALLOWED_HOSTS` explícito y `collectstatic`.
