"""Panel de gestion de usuarios: alta, edicion y baja logica."""

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import UserServiceError, deactivate_user

from .factories import UserFactory

pytestmark = pytest.mark.django_db


def test_alta_de_usuario(client, gestor_centro, centro, rol_mostrador):
    client.force_login(gestor_centro)

    respuesta = client.post(
        reverse("accounts:user_create"),
        {
            "email": "Nueva@Ejemplo.ES",
            "first_name": "Nueva",
            "last_name": "Agente",
            "phone": "600123456",
            "role": rol_mostrador.pk,
            "offices": [centro.pk],
        },
    )

    assert respuesta.status_code == 302
    usuario = User.objects.get(email="nueva@ejemplo.es")  # se normaliza a minusculas
    assert usuario.role == rol_mostrador
    assert list(usuario.offices.all()) == [centro]
    assert usuario.is_active


def test_al_alta_no_se_le_pone_contrasena_conocida(client, gestor_centro, centro):
    """La estrena el propio usuario por correo; nadie se la dicta por telefono."""
    client.force_login(gestor_centro)

    client.post(
        reverse("accounts:user_create"),
        {
            "email": "nueva@ejemplo.es",
            "first_name": "Nueva",
            "last_name": "Agente",
            "phone": "",
            "role": "",
            "offices": [centro.pk],
        },
    )

    usuario = User.objects.get(email="nueva@ejemplo.es")
    for intento in ("", "admin", "nueva@ejemplo.es", "12345678"):
        assert not usuario.check_password(intento)

    # Y puede estrenarla con el flujo normal de recuperacion.
    client.post(reverse("accounts:password_reset"), {"email": usuario.email})
    assert len(mail.outbox) == 1


def test_no_se_repite_el_correo(client, gestor_centro, centro, agente_centro):
    client.force_login(gestor_centro)

    respuesta = client.post(
        reverse("accounts:user_create"),
        {
            "email": agente_centro.email.upper(),
            "first_name": "Duplicada",
            "last_name": "Agente",
            "phone": "",
            "role": "",
            "offices": [centro.pk],
        },
    )

    assert respuesta.status_code == 200
    assert "email" in respuesta.context["form"].errors


def test_desactivar_no_borra(client, gestor_centro, agente_centro):
    client.force_login(gestor_centro)

    respuesta = client.post(reverse("accounts:user_deactivate", args=[agente_centro.pk]))

    agente_centro.refresh_from_db()
    assert respuesta.status_code == 302
    assert agente_centro.is_active is False
    assert User.objects.filter(pk=agente_centro.pk).exists(), "el usuario nunca se borra"


def test_reactivar(client, gestor_centro, agente_centro):
    agente_centro.is_active = False
    agente_centro.save(update_fields=["is_active"])
    client.force_login(gestor_centro)

    client.post(reverse("accounts:user_activate", args=[agente_centro.pk]))

    agente_centro.refresh_from_db()
    assert agente_centro.is_active is True


def test_no_puedes_desactivarte_a_ti_mismo(gestor_centro):
    """Dejarse fuera del sistema por accidente no deberia ser posible."""
    with pytest.raises(UserServiceError):
        deactivate_user(user=gestor_centro, actor=gestor_centro)


def test_un_gestor_no_desactiva_a_un_superusuario(gestor_centro, centro):
    jefe = UserFactory(email="jefe@ejemplo.es", is_superuser=True, offices=[centro])

    with pytest.raises(UserServiceError):
        deactivate_user(user=jefe, actor=gestor_centro)


def test_el_listado_solo_muestra_usuarios_de_tus_oficinas(
    client, gestor_centro, agente_centro, agente_norte
):
    client.force_login(gestor_centro)

    contenido = client.get(reverse("accounts:user_list")).content.decode()

    assert agente_centro.email in contenido
    assert agente_norte.email not in contenido


def test_el_listado_filtra_sin_recargar(client, gestor_centro, agente_centro):
    client.force_login(gestor_centro)

    respuesta = client.get(
        reverse("accounts:user_list"),
        {"q": agente_centro.email},
        headers={"hx-request": "true"},
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in contenido
    assert agente_centro.email in contenido


def test_el_superusuario_ve_a_todo_el_mundo(client, superusuario, agente_centro, agente_norte):
    client.force_login(superusuario)

    contenido = client.get(reverse("accounts:user_list")).content.decode()

    assert agente_centro.email in contenido
    assert agente_norte.email in contenido
