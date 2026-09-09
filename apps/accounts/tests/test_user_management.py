"""Panel de gestion de usuarios: alta, edicion y baja logica."""

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import UserServiceError, deactivate_user

from .factories import UserFactory

pytestmark = pytest.mark.django_db


def test_alta_de_usuario(client, gestor_palma, palma, rol_mostrador):
    client.force_login(gestor_palma)

    respuesta = client.post(
        reverse("accounts:user_create"),
        {
            "email": "Nueva@Ejemplo.ES",
            "first_name": "Nueva",
            "last_name": "Agente",
            "phone": "600123456",
            "role": rol_mostrador.pk,
            "offices": [palma.pk],
        },
    )

    assert respuesta.status_code == 302
    usuario = User.objects.get(email="nueva@ejemplo.es")  # se normaliza a minusculas
    assert usuario.role == rol_mostrador
    assert list(usuario.offices.all()) == [palma]
    assert usuario.is_active


def test_al_alta_no_se_le_pone_contrasena_conocida(client, gestor_palma, palma):
    """La estrena el propio usuario por correo; nadie se la dicta por telefono."""
    client.force_login(gestor_palma)

    client.post(
        reverse("accounts:user_create"),
        {
            "email": "nueva@ejemplo.es",
            "first_name": "Nueva",
            "last_name": "Agente",
            "phone": "",
            "role": "",
            "offices": [palma.pk],
        },
    )

    usuario = User.objects.get(email="nueva@ejemplo.es")
    for intento in ("", "admin", "nueva@ejemplo.es", "12345678"):
        assert not usuario.check_password(intento)

    # Y puede estrenarla con el flujo normal de recuperacion.
    client.post(reverse("accounts:password_reset"), {"email": usuario.email})
    assert len(mail.outbox) == 1


def test_no_se_repite_el_correo(client, gestor_palma, palma, agente_palma):
    client.force_login(gestor_palma)

    respuesta = client.post(
        reverse("accounts:user_create"),
        {
            "email": agente_palma.email.upper(),
            "first_name": "Duplicada",
            "last_name": "Agente",
            "phone": "",
            "role": "",
            "offices": [palma.pk],
        },
    )

    assert respuesta.status_code == 200
    assert "email" in respuesta.context["form"].errors


def test_desactivar_no_borra(client, gestor_palma, agente_palma):
    client.force_login(gestor_palma)

    respuesta = client.post(reverse("accounts:user_deactivate", args=[agente_palma.pk]))

    agente_palma.refresh_from_db()
    assert respuesta.status_code == 302
    assert agente_palma.is_active is False
    assert User.objects.filter(pk=agente_palma.pk).exists(), "el usuario nunca se borra"


def test_reactivar(client, gestor_palma, agente_palma):
    agente_palma.is_active = False
    agente_palma.save(update_fields=["is_active"])
    client.force_login(gestor_palma)

    client.post(reverse("accounts:user_activate", args=[agente_palma.pk]))

    agente_palma.refresh_from_db()
    assert agente_palma.is_active is True


def test_no_puedes_desactivarte_a_ti_mismo(gestor_palma):
    """Dejarse fuera del sistema por accidente no deberia ser posible."""
    with pytest.raises(UserServiceError):
        deactivate_user(user=gestor_palma, actor=gestor_palma)


def test_un_gestor_no_desactiva_a_un_superusuario(gestor_palma, palma):
    jefe = UserFactory(email="jefe@ejemplo.es", is_superuser=True, offices=[palma])

    with pytest.raises(UserServiceError):
        deactivate_user(user=jefe, actor=gestor_palma)


def test_el_listado_solo_muestra_usuarios_de_tus_oficinas(
    client, gestor_palma, agente_palma, agente_alcudia
):
    client.force_login(gestor_palma)

    contenido = client.get(reverse("accounts:user_list")).content.decode()

    assert agente_palma.email in contenido
    assert agente_alcudia.email not in contenido


def test_el_listado_filtra_sin_recargar(client, gestor_palma, agente_palma):
    client.force_login(gestor_palma)

    respuesta = client.get(
        reverse("accounts:user_list"),
        {"q": agente_palma.email},
        headers={"hx-request": "true"},
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in contenido
    assert agente_palma.email in contenido


def test_el_superusuario_ve_a_todo_el_mundo(client, superusuario, agente_palma, agente_alcudia):
    client.force_login(superusuario)

    contenido = client.get(reverse("accounts:user_list")).content.decode()

    assert agente_palma.email in contenido
    assert agente_alcudia.email in contenido
