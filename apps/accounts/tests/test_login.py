"""Acceso: entrar, no poder entrar, y que no haya puerta trasera."""

import pytest
from django.core import mail
from django.urls import reverse

from .factories import UserFactory

pytestmark = pytest.mark.django_db

LOGIN = "accounts:login"


def _entrar(client, email, contrasena):
    return client.post(reverse(LOGIN), {"username": email, "password": contrasena})


def test_se_entra_con_el_correo(client, agente_palma, contrasena):
    respuesta = _entrar(client, agente_palma.email, contrasena)

    assert respuesta.status_code == 302
    assert respuesta.wsgi_request.user.is_authenticated


def test_el_correo_no_distingue_mayusculas(client, agente_palma, contrasena):
    respuesta = _entrar(client, agente_palma.email.upper(), contrasena)

    assert respuesta.status_code == 302


def test_un_usuario_desactivado_no_entra_aunque_acierte(client, agente_palma, contrasena):
    """La contrasena es correcta: lo que corta es is_active."""
    agente_palma.is_active = False
    agente_palma.save(update_fields=["is_active"])

    respuesta = _entrar(client, agente_palma.email, contrasena)

    assert respuesta.status_code == 200  # se queda en el formulario
    assert not respuesta.wsgi_request.user.is_authenticated


def test_contrasena_incorrecta(client, agente_palma):
    respuesta = _entrar(client, agente_palma.email, "no-es-esta")

    assert respuesta.status_code == 200
    assert not respuesta.wsgi_request.user.is_authenticated


def test_el_error_no_dice_si_el_correo_existe(client, agente_palma):
    """Mismo mensaje para correo inexistente y contrasena mala."""
    inexistente = _entrar(client, "nadie@ejemplo.es", "loquesea")
    mala = _entrar(client, agente_palma.email, "loquesea")

    assert inexistente.context["form"].errors == mala.context["form"].errors


def test_salir_solo_por_post(client, agente_palma):
    client.force_login(agente_palma)

    assert client.get(reverse("accounts:logout")).status_code == 405

    respuesta = client.post(reverse("accounts:logout"))
    assert respuesta.status_code == 302
    assert not respuesta.wsgi_request.user.is_authenticated


# --- alta publica -----------------------------------------------------------

RUTAS_DE_REGISTRO = [
    "/registro/",
    "/signup/",
    "/register/",
    "/alta/",
    "/accounts/signup/",
    "/accounts/register/",
    "/usuarios/registro/",
]


@pytest.mark.parametrize("ruta", RUTAS_DE_REGISTRO)
def test_no_hay_alta_publica(client, ruta):
    respuesta = client.get(ruta)

    assert respuesta.status_code in (404, 410), f"{ruta} no deberia dar acceso"
    if respuesta.status_code == 410:
        assert b"privado" in respuesta.content


def test_ninguna_url_del_proyecto_se_llama_registro():
    """Que no aparezca una vista de alta por la puerta de atras."""
    from django.urls import get_resolver

    sospechosos = ("signup", "register", "registro_publico")
    nombres = [
        nombre
        for nombre in get_resolver().reverse_dict
        if isinstance(nombre, str) and not nombre.startswith("registro_deshabilitado")
    ]

    assert not [n for n in nombres if any(s in n.lower() for s in sospechosos)]


# --- recuperacion de contrasena ---------------------------------------------


def test_el_reseteo_envia_correo_a_una_cuenta_activa(client, agente_palma):
    respuesta = client.post(reverse("accounts:password_reset"), {"email": agente_palma.email})

    assert respuesta.status_code == 302
    assert len(mail.outbox) == 1
    assert agente_palma.email in mail.outbox[0].to
    assert "contrasena/nueva/" in mail.outbox[0].body


def test_el_reseteo_calla_con_una_cuenta_desactivada(client, agente_palma):
    """No se envia nada, pero la respuesta es la misma: no se filtra quien existe."""
    agente_palma.is_active = False
    agente_palma.save(update_fields=["is_active"])

    respuesta = client.post(reverse("accounts:password_reset"), {"email": agente_palma.email})

    assert respuesta.status_code == 302
    assert len(mail.outbox) == 0


def test_el_reseteo_calla_con_un_correo_inexistente(client):
    respuesta = client.post(reverse("accounts:password_reset"), {"email": "nadie@ejemplo.es"})

    assert respuesta.status_code == 302
    assert len(mail.outbox) == 0


def test_cambiar_contrasena_requiere_sesion(client):
    respuesta = client.get(reverse("accounts:password_change"))

    assert respuesta.status_code == 302
    assert reverse(LOGIN) in respuesta.headers["Location"]


def test_cambiar_contrasena_con_sesion(client, agente_palma, contrasena):
    client.force_login(agente_palma)

    respuesta = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": contrasena,
            "new_password1": "otra-contrasena-larga-99",
            "new_password2": "otra-contrasena-larga-99",
        },
    )

    agente_palma.refresh_from_db()
    assert respuesta.status_code == 302
    assert agente_palma.check_password("otra-contrasena-larga-99")


# --- bloqueo por intentos fallidos ------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_bloqueo_tras_varios_intentos_fallidos(client, settings, contrasena):
    """Al superar el limite, ni la contrasena correcta abre la puerta."""
    from axes.models import AccessAttempt

    # El resto de la suite corre con axes apagado (ver conftest): aqui se
    # enciende a proposito porque es justo lo que se esta probando.
    settings.AXES_ENABLED = True
    settings.AXES_FAILURE_LIMIT = 3
    usuario = UserFactory(email="bloqueable@ejemplo.es")

    for _ in range(3):
        _entrar(client, usuario.email, "mal")

    respuesta = _entrar(client, usuario.email, contrasena)

    # 429 (demasiadas peticiones), que es lo que devuelve axes al bloquear.
    assert respuesta.status_code == 429
    assert not respuesta.wsgi_request.user.is_authenticated
    assert AccessAttempt.objects.filter(username=usuario.email).exists()


@pytest.mark.django_db(transaction=True)
def test_por_debajo_del_limite_todavia_se_entra(client, settings, contrasena):
    settings.AXES_ENABLED = True
    settings.AXES_FAILURE_LIMIT = 3
    usuario = UserFactory(email="casi@ejemplo.es")

    for _ in range(2):
        _entrar(client, usuario.email, "mal")

    respuesta = _entrar(client, usuario.email, contrasena)

    assert respuesta.status_code == 302
    assert respuesta.wsgi_request.user.is_authenticated
