"""Configuracion global de pytest y fixtures compartidas."""

from functools import partial

import pytest

from apps.accounts.tests.factories import CONTRASENA, OfficeFactory, RoleFactory, UserFactory


@pytest.fixture(autouse=True)
def _sin_llamadas_externas(settings):
    """Ningun test debe salir a internet ni encolar tareas reales por accidente."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture(autouse=True)
def _sin_bloqueo_por_intentos(settings):
    """django-axes apagado salvo en los tests que lo prueban.

    Encendido, los tests que fallan un login a proposito acabarian bloqueando
    a los siguientes: los intentos se acumulan por IP, y en tests la IP es una.
    """
    settings.AXES_ENABLED = False


#: El backend con el que se crean las sesiones reales (lo elige `authenticate`).
BACKEND_DE_SESION = "django.contrib.auth.backends.ModelBackend"


@pytest.fixture
def client(client):
    """`force_login()` sin mas cogeria el primer backend, que es el de axes.

    Ese backend solo bloquea intentos: no sabe recuperar al usuario de la
    sesion, asi que la peticion siguiente llegaria como anonima. Aqui se fija
    el mismo backend que usa el login de verdad.
    """
    client.force_login = partial(client.force_login, backend=BACKEND_DE_SESION)
    return client


@pytest.fixture
def contrasena():
    return CONTRASENA


@pytest.fixture
def centro(db):
    return OfficeFactory(code="centro", name="Oficina Centro")


@pytest.fixture
def norte(db):
    return OfficeFactory(code="norte", name="Oficina Norte")


@pytest.fixture
def rol_gestor(db):
    """Rol con permiso para gestionar usuarios."""
    return RoleFactory(code="gestor", name="Gestor", permissions=["accounts.manage_users"])


@pytest.fixture
def rol_mostrador(db):
    """Rol sin ningun permiso de administracion."""
    return RoleFactory(code="mostrador-test", name="Mostrador")


@pytest.fixture
def gestor_centro(db, centro, rol_gestor):
    return UserFactory(email="gestor@centro.es", role=rol_gestor, offices=[centro])


@pytest.fixture
def agente_centro(db, centro, rol_mostrador):
    return UserFactory(email="agente@centro.es", role=rol_mostrador, offices=[centro])


@pytest.fixture
def agente_norte(db, norte, rol_mostrador):
    return UserFactory(email="agente@norte.es", role=rol_mostrador, offices=[norte])


#: Permisos de quien mantiene los maestros: oficinas, grupos y categorias.
PERMISOS_MAESTROS = [
    "offices.view_office",
    "offices.add_office",
    "offices.change_office",
    "offices.view_officepool",
    "offices.add_officepool",
    "offices.change_officepool",
    "fleet.view_vehiclecategory",
    "fleet.add_vehiclecategory",
    "fleet.change_vehiclecategory",
    "fleet.view_vehicle",
    "fleet.add_vehicle",
    "fleet.change_vehicle",
    "fleet.view_vehicleblock",
    "fleet.add_vehicleblock",
    "fleet.change_vehicleblock",
    "fleet.delete_vehicleblock",
    "customers.view_customer",
    "customers.add_customer",
    "customers.change_customer",
    "pricing.view_extra",
    "pricing.add_extra",
    "pricing.change_extra",
]


@pytest.fixture
def rol_maestros(db):
    return RoleFactory(code="maestros", name="Maestros", permissions=PERMISOS_MAESTROS)


@pytest.fixture
def gestor_maestros(db, centro, rol_maestros):
    """Usuario que puede mantener oficinas y categorias."""
    return UserFactory(email="maestros@ejemplo.es", role=rol_maestros, offices=[centro])


#: Quien mantiene el catalogo de precios.
PERMISOS_TARIFAS = [
    "pricing.view_rate",
    "pricing.add_rate",
    "pricing.change_rate",
    "pricing.view_season",
    "pricing.add_season",
    "pricing.change_season",
    "pricing.view_supplement",
    "pricing.add_supplement",
    "pricing.change_supplement",
    "pricing.view_discount",
    "pricing.add_discount",
    "pricing.change_discount",
    "pricing.view_extra",
    "pricing.add_extra",
    "pricing.change_extra",
]


@pytest.fixture
def gestor_tarifas(db, centro):
    """Usuario que puede configurar tarifas, temporadas y suplementos."""
    rol = RoleFactory(code="tarifas", name="Tarifas", permissions=PERMISOS_TARIFAS)
    return UserFactory(email="tarifas@ejemplo.es", role=rol, offices=[centro])


@pytest.fixture
def superusuario(db):
    return UserFactory(email="jefe@ejemplo.es", is_staff=True, is_superuser=True)


@pytest.fixture
def usuario(agente_centro):
    """Un usuario cualquiera con sesion iniciable, para pantallas sin permisos."""
    return agente_centro


@pytest.fixture
def client_con_sesion(client, usuario):
    """Cliente ya autenticado. La aplicacion es privada de principio a fin."""
    client.force_login(usuario)
    return client
