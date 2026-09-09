"""El usuario actual no puede filtrarse de una peticion a otra."""

import threading

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.middleware import CurrentUserMiddleware, current_user, get_current_user


class UsuarioFalso:
    """Basta con parecer un usuario: el contextvar no toca la base de datos."""

    is_authenticated = True

    def __init__(self, username):
        self.username = username

    def __repr__(self):
        return f"<UsuarioFalso {self.username}>"


def _peticion(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def test_expone_el_usuario_durante_la_request():
    usuario = UsuarioFalso("mostrador")
    visto = {}

    def get_response(request):
        visto["usuario"] = get_current_user()
        return HttpResponse()

    CurrentUserMiddleware(get_response)(_peticion(usuario))

    assert visto["usuario"] is usuario


def test_limpia_el_usuario_al_terminar_la_request():
    """Sin reset, el siguiente request del mismo hilo heredaria el usuario."""
    middleware = CurrentUserMiddleware(lambda request: HttpResponse())

    middleware(_peticion(UsuarioFalso("mostrador")))

    assert get_current_user() is None


def test_usuario_anonimo_no_cuenta_como_usuario():
    class Anonimo:
        is_authenticated = False

    visto = {}

    def get_response(request):
        visto["usuario"] = get_current_user()
        return HttpResponse()

    CurrentUserMiddleware(get_response)(_peticion(Anonimo()))

    assert visto["usuario"] is None


def test_no_filtra_usuario_entre_peticiones_concurrentes():
    """Dos peticiones simultaneas, cada una con su usuario.

    La barrera obliga a que los dos hilos esten dentro del middleware a la vez:
    con un global o un atributo de modulo, el segundo pisaria al primero y la
    comprobacion fallaria.
    """
    usuarios = [UsuarioFalso(f"usuario-{i}") for i in range(8)]
    barrera = threading.Barrier(len(usuarios), timeout=10)
    visto: dict[str, object] = {}
    fallos: list[BaseException] = []

    def get_response(request):
        # Todos han fijado ya su usuario; ahora todos leen.
        barrera.wait()
        visto[request.user.username] = get_current_user()
        return HttpResponse()

    middleware = CurrentUserMiddleware(get_response)

    def correr(usuario):
        try:
            middleware(_peticion(usuario))
        except BaseException as exc:  # el fallo real se ve en el assert de abajo
            fallos.append(exc)

    hilos = [threading.Thread(target=correr, args=(u,)) for u in usuarios]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=15)

    assert not fallos, fallos
    assert visto == {u.username: u for u in usuarios}
    assert get_current_user() is None


def test_context_manager_para_tareas_sin_request():
    usuario = UsuarioFalso("celery")

    with current_user(usuario):
        assert get_current_user() is usuario

    assert get_current_user() is None


@pytest.mark.django_db
def test_el_usuario_de_la_sesion_llega_al_contextvar(client, django_user_model):
    """Comprobacion de extremo a extremo: middleware real, request real."""
    usuario = django_user_model.objects.create_user(username="ana", password="secreto123")
    client.force_login(usuario)

    respuesta = client.get("/")

    assert respuesta.status_code == 200
    assert respuesta.context["user"].username == "ana"
