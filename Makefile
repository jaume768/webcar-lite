# Atajos de desarrollo. Todo se ejecuta dentro de docker compose.
COMPOSE := docker compose --profile dev
RUN     := $(COMPOSE) run --rm web
EXEC    := $(COMPOSE) exec web

.DEFAULT_GOAL := help
.PHONY: help env build up down logs coverage shell bash dbshell test lint format migrate makemigrations manage sync_roles seed superuser collectstatic clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Crea .env desde .env.example si no existe
	@test -f .env || (cp .env.example .env && echo "Creado .env desde .env.example")

build: env ## Construye las imagenes
	$(COMPOSE) build

up: env ## Levanta el entorno completo (app en http://localhost:8000)
	$(COMPOSE) up -d --build
	@echo "Esperando a que la app responda en /health/ ..."
	@$(COMPOSE) exec -T web sh -c 'for i in $$(seq 1 60); do curl -fs http://localhost:8000/health/ >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1' \
		&& echo "Listo: http://localhost:$$(sed -n 's/^WEB_PORT=//p' .env | tail -1 | grep . || echo 8000)" \
		|| (echo "La app no responde. Revisa: make logs"; exit 1)

down: ## Para los contenedores (conserva los datos)
	$(COMPOSE) down

logs: ## Sigue los logs de todos los servicios
	$(COMPOSE) logs -f

shell: ## Shell de Django (contenedor web)
	$(EXEC) python manage.py shell

bash: ## Shell de sistema en el contenedor web
	$(EXEC) bash

dbshell: ## psql contra la base de datos
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-crm} -d $${POSTGRES_DB:-crm}

test: env ## Ejecuta pytest contra Postgres real
	$(COMPOSE) up -d db redis
	$(RUN) pytest $(ARGS)

coverage: env ## Ejecuta los tests con informe de cobertura
	$(COMPOSE) up -d db redis
	$(RUN) pytest --cov --cov-report=term-missing $(ARGS)

lint: ## ruff check + comprobacion de formato
	$(RUN) sh -c "ruff check . && ruff format --check ."

format: ## Aplica formato y arreglos automaticos
	$(RUN) sh -c "ruff format . && ruff check --fix ."

migrate: ## Aplica migraciones
	$(EXEC) python manage.py migrate

makemigrations: ## Genera migraciones
	$(EXEC) python manage.py makemigrations $(ARGS)

manage: ## Ejecuta un comando de Django (ARGS="sync_roles")
	$(EXEC) python manage.py $(ARGS)

sync_roles: ## Aplica los roles del sistema declarados en accounts/roles.py
	$(EXEC) python manage.py sync_roles $(ARGS)

seed: ## Carga datos iniciales de desarrollo
	$(EXEC) python manage.py seed $(ARGS)

superuser: ## Crea un superusuario
	$(EXEC) python manage.py createsuperuser

collectstatic: ## Recopila estaticos
	$(EXEC) python manage.py collectstatic --noinput

clean: ## Para todo y borra volumenes (BORRA LA BASE DE DATOS)
	$(COMPOSE) down -v
