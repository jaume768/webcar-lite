"""Ajustes comunes a todos los entornos."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import environ
import structlog
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    RENTAL_COURTESY_MINUTES=(int, 59),
    DEFAULT_TAX_RATE=(Decimal, Decimal("21.00")),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ---------------------------------------------------------------- apps
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "template_partials",
    "django_htmx",
    "django_filters",
    "axes",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.offices",
    "apps.customers",
    "apps.fleet",
    "apps.pricing",
    "apps.availability",
    "apps.reservations",
    "apps.operations",
    "apps.billing",
    "apps.contracts",
    "apps.compliance",
    "apps.auditlog",
    "apps.settings_app",
    "apps.notifications",
    "apps.booking_api",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.middleware.InterfaceLanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.CurrentUserMiddleware",
    # Sistema privado: se entra por defecto, y lo publico se marca
    # una a una con @login_not_required. Asi no se olvida ninguna.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
    # El ultimo: necesita la respuesta ya formada para marcar el intento.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "apps" / "core" / "templates"],
        # Sin APP_DIRS: el loader de template_partials envuelve a los demas y
        # es incompatible con APP_DIRS, que ya instala su propio loader.
        "OPTIONS": {
            "loaders": [
                (
                    "template_partials.loader.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.ui",
            ],
            "builtins": ["template_partials.templatetags.partials"],
        },
    },
]

# ---------------------------------------------------------------- datos
# Postgres siempre, tambien en tests. El dominio usa tstzrange, btree_gist
# y constraints de exclusion: SQLite no vale ni como atajo.
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

# Axes primero: no autentica a nadie, solo corta el intento si la cuenta esta
# bloqueada, y para eso tiene que mirar antes que nadie. ModelBackend es quien
# comprueba la contrasena. RolePermissionsBackend tampoco autentica: suma los
# permisos que trae el rol.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "apps.accounts.backends.RolePermissionsBackend",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # un dia

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------- i18n
LANGUAGE_CODE = "es"
# El panel de gestion va en espanol (lo fija InterfaceLanguageMiddleware). Los
# otros idiomas son los del cliente: correos, factura, contrato y pago online
# salen en el suyo con translation.override().
LANGUAGES = [("es", "Español"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français")]
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------- estaticos
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Documentos de clientes (carnet, DNI). Fuera de MEDIA_ROOT a proposito: no
# tienen URL publica ni los sirve el servidor de estaticos. Se descargan por una
# vista que comprueba permisos, nunca adivinando la ruta.
_private_media = env.str("DJANGO_PRIVATE_MEDIA_ROOT", default="")
PRIVATE_MEDIA_ROOT = Path(_private_media) if _private_media else BASE_DIR / "private-media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private": {
        "BACKEND": "apps.core.storage.PrivateFileSystemStorage",
        "OPTIONS": {"location": str(PRIVATE_MEDIA_ROOT)},
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Tamano maximo de un documento subido. Un carnet escaneado no pasa de aqui, y
# el limite evita llenar el disco desde el formulario.
MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=10)

# ---------------------------------------------------------------- cache / redis
REDIS_URL = env("REDIS_URL")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ---------------------------------------------------------------- celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240

# Tareas programadas (servicio `beat` de docker compose).
CELERY_BEAT_SCHEDULE = {
    "recordatorios-de-recogida": {
        "task": "apps.notifications.tasks.send_pickup_reminders",
        "schedule": crontab(minute=5),
    },
    "reintentar-partes-ses": {
        "task": "apps.compliance.tasks.retry_pending_ses",
        "schedule": crontab(minute="*/30"),
    },
    "caducar-enlaces-de-pago": {
        "task": "apps.billing.tasks.expire_payment_links",
        "schedule": crontab(minute=20),
    },
}

# Un fallo de CSRF en el login de alguien que ya ha entrado es un doble envio,
# no un ataque: ver accounts.views.csrf_failure.
CSRF_FAILURE_VIEW = "apps.accounts.views.csrf_failure"

# ---------------------------------------------------------------- bloqueo
# Intentos fallidos antes de bloquear. Se cuenta la combinacion IP + usuario:
# por usuario solo, cualquiera podria dejar fuera a un companero de mostrador.
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/lockout.html"
AXES_VERBOSE = True
# El nombre de usuario es el correo.
AXES_USERNAME_FORM_FIELD = "username"

# ---------------------------------------------------------------- interfaz
# De donde salen las oficinas que puede usar cada usuario. Unico punto de
# verdad para el selector, la validacion del cambio y el scope de datos.
CORE_OFFICE_PROVIDER = "apps.offices.selectors.office_choices_for_request"

# ---------------------------------------------------------------- dominio
# Margen de cortesia para el calculo de dias de alquiler (24h + margen).
# Lo consume pricing.services.rental_days(); aqui solo vive el valor por defecto.
RENTAL_COURTESY_MINUTES = env("RENTAL_COURTESY_MINUTES")
DEFAULT_CURRENCY = "EUR"

# Modo demostracion: ensena la landing con acceso de un clic a una cuenta de
# prueba. Apagado por defecto y **nunca** se enciende en produccion: cualquiera
# que llegue a la portada entraria en el sistema.
DEMO_MODE = env.bool("DEMO_MODE", default=False)
DEMO_EMAIL = env("DEMO_EMAIL", default="demo@webcar.example")
DEMO_PASSWORD = env("DEMO_PASSWORD", default="demo-webcar-2026")

# Cada cuantos segundos se refresca solo el panel de mostrador.
DASHBOARD_REFRESH_SECONDS = env.int("DASHBOARD_REFRESH_SECONDS", default=120)

# --- Cargos de devolucion --------------------------------------------------
# Precio por kilometro pasado del limite incluido.
EXTRA_KM_PRICE = env("EXTRA_KM_PRICE", default="0.15")
# Precio del litro con el que se cobra el combustible que falta.
FUEL_PRICE_PER_LITER = env("FUEL_PRICE_PER_LITER", default="1.60")
# Capacidad de deposito por defecto, para vehiculos sin dato propio.
DEFAULT_TANK_LITERS = env.int("DEFAULT_TANK_LITERS", default=50)
# Estado del vehiculo tras la devolucion: "cleaning" o "available".
VEHICLE_STATUS_AFTER_CHECKOUT = env("VEHICLE_STATUS_AFTER_CHECKOUT", default="cleaning")

# Formato del numero de reserva. Marcadores disponibles: {year} y {sequence}.
# Si lleva {year}, la numeracion se reinicia cada ano; si no, es continua.
# La serie no tiene huecos: la lleva un contador con bloqueo, no una secuencia
# de Postgres (que salta numeros al deshacer una transaccion).
RESERVATION_NUMBER_FORMAT = env("RESERVATION_NUMBER_FORMAT", default="R{year}-{sequence:05d}")

# Fianza y franquicia por defecto de una reserva nueva, en euros.
RESERVATION_DEFAULT_DEPOSIT = env("RESERVATION_DEFAULT_DEPOSIT", default="150.00")
RESERVATION_DEFAULT_FRANCHISE = env("RESERVATION_DEFAULT_FRANCHISE", default="600.00")

# Minutos de rotacion entre dos alquileres del mismo vehiculo: limpieza y
# revision. Cuentan como ocupacion, asi que dos reservas seguidas del mismo
# coche tienen que dejar al menos este hueco. Lo consume
# availability.services; cada reserva guarda el valor que se le aplico.
VEHICLE_ROTATION_MINUTES = env.int("VEHICLE_ROTATION_MINUTES", default=60)

# --- Correo ------------------------------------------------------------------
# Con BREVO_API_KEY los correos al cliente salen por la API de Brevo. Sin ella,
# consola en desarrollo y SMTP en produccion.
BREVO_API_KEY = env("BREVO_API_KEY", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@localhost")
# Direccion a la que responde el cliente (la de la oficina, normalmente).
EMAIL_REPLY_TO = env("EMAIL_REPLY_TO", default="")

# --- Pagos online ----------------------------------------------------------
# URL publica de la aplicacion: con ella se arman los enlaces de pago y las
# URL de vuelta y de notificacion que se dan a las pasarelas.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000").rstrip("/")
# Horas que vale un enlace de pago antes de caducar.
ONLINE_PAYMENT_LINK_HOURS = env.int("ONLINE_PAYMENT_LINK_HOURS", default=72)
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
REDSYS_MERCHANT_CODE = env("REDSYS_MERCHANT_CODE", default="")
REDSYS_TERMINAL = env("REDSYS_TERMINAL", default="1")
REDSYS_SECRET_KEY = env("REDSYS_SECRET_KEY", default="")
# Entorno de pruebas del banco (sis-t). En produccion, False.
REDSYS_TEST = env.bool("REDSYS_TEST", default=True)

# --- API de reservas para la web del cliente -------------------------------
# Minutos minimos entre el momento de reservar y la recogida.
BOOKING_API_MIN_LEAD_MINUTES = env.int("BOOKING_API_MIN_LEAD_MINUTES", default=120)
# Peticiones por minuto y cliente de la API. 0 desactiva el limite.
BOOKING_API_RATE_LIMIT_PER_MINUTE = env.int("BOOKING_API_RATE_LIMIT_PER_MINUTE", default=120)

# --- SES.Hospedajes (RD 933/2021) ------------------------------------------
# Sin SES_ENABLED el parte se valida y se guarda con su XML, pero no se envia:
# queda marcado como simulado. Las credenciales y el endpoint los da el
# Ministerio del Interior al dar de alta el establecimiento en la sede.
SES_ENABLED = env.bool("SES_ENABLED", default=False)
SES_ENDPOINT = env("SES_ENDPOINT", default="")
SES_USER = env("SES_USER", default="")
SES_PASSWORD = env("SES_PASSWORD", default="")
SES_LANDLORD_CODE = env("SES_LANDLORD_CODE", default="")
SES_APPLICATION = env("SES_APPLICATION", default="RentFlow")
# Horas desde la entrega del coche para comunicar el contrato.
SES_DEADLINE_HOURS = env.int("SES_DEADLINE_HOURS", default=24)

# Gestion de una multa que se repercute al cliente, sin IVA.
FINE_ADMIN_FEE = env("FINE_ADMIN_FEE", default="30.00")

# IVA por defecto de las lineas de alquiler. Los extras y los suplementos
# llevan el suyo propio, porque no todos tributan igual.
DEFAULT_TAX_RATE = env("DEFAULT_TAX_RATE")

# ---------------------------------------------------------------- logging
# Se aplica a los registros que no vienen de structlog (Django, gunicorn, celery)
# para que salgan con los mismos campos.
_PRE_CHAIN = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # ProcessorFormatter necesita el renderer ya instanciado: pasarlo como
        # ruta de importacion deja un formatter roto que solo falla al loguear.
        "plain": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(colors=False),
            "foreign_pre_chain": _PRE_CHAIN,
        },
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
            "foreign_pre_chain": _PRE_CHAIN,
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": env("DJANGO_LOG_FORMAT", default="plain"),
        },
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
    },
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
