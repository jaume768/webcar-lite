# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- assets
FROM node:22-bookworm-slim AS assets
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY assets ./assets
COPY static ./static
COPY apps ./apps
RUN npm run build

# ---------------------------------------------------------------- build
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml README.md ./
# Instala solo las dependencias: el codigo se monta o se copia despues, para que
# un cambio en una vista no invalide la capa de dependencias.
ARG INSTALL_DEV=false
RUN python -c "\
import tomllib, pathlib; \
d = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']; \
pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(d['dependencies'])); \
pathlib.Path('/tmp/requirements-dev.txt').write_text('\n'.join(d['optional-dependencies']['dev']))"
RUN pip install -r /tmp/requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -r /tmp/requirements-dev.txt; fi

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# Dependencias de sistema de WeasyPrint (pango/cairo) y tipografias.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        gettext \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --shell /bin/bash app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .
COPY --from=assets --chown=app:app /app/static/css/app.css /app/static/css/app.css
COPY --from=assets --chown=app:app /app/static/js /app/static/js
COPY --from=assets --chown=app:app /app/static/fonts /app/static/fonts

RUN mkdir -p /app/staticfiles /app/media

# El manifiesto de whitenoise se genera en el build: la imagen arranca ya servible
# y un fallo de estaticos se ve aqui, no en el primer request de produccion.
# Estos valores solo existen durante el build; collectstatic no toca la base de datos.
# Las traducciones del cliente (correos, factura) se compilan aqui tambien.
RUN export DJANGO_SECRET_KEY=solo-para-collectstatic-en-build \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    REDIS_URL=redis://localhost:6379/0 \
    && python manage.py compilemessages --ignore=.venv \
    && python manage.py collectstatic --noinput \
    && chown -R app:app /app/staticfiles /app/media

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
