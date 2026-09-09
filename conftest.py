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
def palma(db):
    return OfficeFactory(code="palma", name="Palma Centro")


@pytest.fixture
def alcudia(db):
    return OfficeFactory(code="alcudia", name="Alcudia Puerto")


@pytest.fixture
def rol_gestor(db):
    """Rol con permiso para gestionar usuarios."""
    return RoleFactory(code="gestor", name="Gestor", permissions=["accounts.manage_users"])


@pytest.fixture
def rol_mostrador(db):
    """Rol sin ningun permiso de administracion."""
    return RoleFactory(code="mostrador-test", name="Mostrador")


@pytest.fixture
def gestor_palma(db, palma, rol_gestor):
    return UserFactory(email="gestor@palma.es", role=rol_gestor, offices=[palma])


@pytest.fixture
def agente_palma(db, palma, rol_mostrador):
    return UserFactory(email="agente@palma.es", role=rol_mostrador, offices=[palma])


@pytest.fixture
def agente_alcudia(db, alcudia, rol_mostrador):
    return UserFactory(email="agente@alcudia.es", role=rol_mostrador, offices=[alcudia])


@pytest.fixture
def superusuario(db):
    return UserFactory(email="jefe@ejemplo.es", is_staff=True, is_superuser=True)


@pytest.fixture
def usuario(agente_palma):
    """Un usuario cualquiera con sesion iniciable, para pantallas sin permisos."""
    return agente_palma


@pytest.fixture
def client_con_sesion(client, usuario):
    """Cliente ya autenticado. La aplicacion es privada de principio a fin."""
    client.force_login(usuario)
    return client
