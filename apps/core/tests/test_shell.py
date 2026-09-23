"""base.html y el shell: con usuario, sin usuario y con oficina activa."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_base_renderiza_sin_usuario(client):
    """La pantalla de acceso usa su propio esqueleto, sin menu ni oficinas."""
    respuesta = client.get(reverse("accounts:login"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Entrar" in contenido
    assert "Navegacion principal" not in contenido
    assert "selector-oficina" not in contenido


def test_base_renderiza_con_usuario(client, agente_centro):
    client.force_login(agente_centro)

    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert agente_centro.email in contenido
    assert 'id="contenido"' in contenido
    assert "Navegacion principal" in contenido


def test_el_menu_oculta_lo_que_el_usuario_no_puede_usar(client, agente_centro, gestor_centro):
    """Ocultar no sustituye a validar, pero tampoco se ensenan puertas cerradas."""
    client.force_login(agente_centro)
    sin_permiso = client.get(reverse("core:home")).content.decode()

    client.force_login(gestor_centro)
    con_permiso = client.get(reverse("core:home")).content.decode()

    assert "Usuarios" not in sin_permiso
    assert "Usuarios" in con_permiso


def test_sin_oficinas_el_selector_lo_dice(client, rol_mostrador):
    from apps.accounts.tests.factories import UserFactory

    huerfano = UserFactory(email="sinoficina@ejemplo.es", role=rol_mostrador)
    client.force_login(huerfano)

    respuesta = client.get(reverse("core:home"))

    assert "Sin oficinas asignadas" in respuesta.content.decode()


def test_el_selector_solo_muestra_las_oficinas_del_usuario(client, agente_centro, norte):
    """La oficina norte existe, pero este usuario no la tiene asignada."""
    client.force_login(agente_centro)

    contenido = client.get(reverse("core:home")).content.decode()

    assert "Oficina Centro" in contenido
    assert "Oficina Norte" not in contenido


def test_cambiar_de_oficina_actualiza_la_sesion(client, agente_centro, centro, norte):
    agente_centro.offices.add(norte)
    client.force_login(agente_centro)

    respuesta = client.post(
        reverse("core:set_active_office"),
        {"office_id": str(norte.pk)},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 200
    assert client.session["active_office_id"] == str(norte.pk)
    assert "Oficina Norte" in respuesta.headers["HX-Trigger"]


def test_el_selector_del_menu_movil_vuelve_con_sus_propios_ids(client, agente_centro, norte):
    """El selector se pinta en la cabecera y en el menu del movil: cada uno
    tiene que recibir de vuelta el suyo, sin ids repetidos en la pagina."""
    agente_centro.offices.add(norte)
    client.force_login(agente_centro)

    movil = client.post(
        reverse("core:set_active_office"),
        {"office_id": str(norte.pk), "variante": "movil"},
        headers={"hx-request": "true"},
    ).content.decode()
    cabecera = client.post(
        reverse("core:set_active_office"),
        {"office_id": str(norte.pk)},
        headers={"hx-request": "true"},
    ).content.decode()

    assert 'id="selector-oficina-movil"' in movil
    assert 'id="selector-oficina"' in cabecera
    assert "selector-oficina-movil" not in cabecera


def test_una_variante_desconocida_no_llega_al_html(client, agente_centro, norte):
    agente_centro.offices.add(norte)
    client.force_login(agente_centro)

    contenido = client.post(
        reverse("core:set_active_office"),
        {"office_id": str(norte.pk), "variante": '"><script>'},
        headers={"hx-request": "true"},
    ).content.decode()

    assert "<script>" not in contenido
    assert 'id="selector-oficina"' in contenido


def test_la_pagina_no_repite_ids_del_selector(client, agente_centro):
    client.force_login(agente_centro)

    contenido = client.get(reverse("core:home")).content.decode()

    assert contenido.count('id="selector-oficina"') == 1
    assert contenido.count('id="selector-oficina-movil"') == 1
    assert "Navegacion rapida" in contenido


def test_no_se_puede_activar_una_oficina_ajena(client, agente_centro, norte):
    """Manipular el POST no abre la puerta a otra oficina."""
    client.force_login(agente_centro)

    respuesta = client.post(reverse("core:set_active_office"), {"office_id": str(norte.pk)})

    assert respuesta.status_code == 403
    assert "active_office_id" not in client.session


def test_una_oficina_retirada_deja_de_estar_activa(client, agente_centro, centro, norte):
    """Si dejan de asignarte una oficina, la sesion deja de valerte."""
    from apps.offices.selectors import get_active_office

    agente_centro.offices.add(norte)
    client.force_login(agente_centro)
    client.post(reverse("core:set_active_office"), {"office_id": str(norte.pk)})

    agente_centro.offices.remove(norte)
    peticion = client.get(reverse("core:home")).wsgi_request

    activa = get_active_office(peticion)
    assert activa == centro


def test_paginas_de_error_propias(client, usuario):
    client.force_login(usuario)

    for codigo in (403, 404):
        respuesta = client.get(reverse("core:ui_kit_error", args=[codigo]))
        assert respuesta.status_code == codigo
        assert str(codigo) in respuesta.content.decode()

    respuesta = client.get(reverse("core:ui_kit_error", args=[500]))
    assert respuesta.status_code == 500
    assert "Algo se ha roto" in respuesta.content.decode()


def test_el_menu_deja_facturacion_y_administracion_al_final():
    from apps.core.navigation import MAIN_NAV

    secciones = [str(seccion.label) for seccion in MAIN_NAV]

    assert secciones[-2:] == ["Facturación", "Administración"]
    assert "Planning" in [str(item.label) for item in MAIN_NAV[1].items]


# ---------------------------------------------------------------------------
# Secciones plegables del menu
# ---------------------------------------------------------------------------


def test_cada_seccion_del_menu_tiene_un_codigo_estable():
    """La etiqueta se traduce; lo que se guarda en el navegador, no."""
    from apps.core.navigation import MAIN_NAV

    codigos = [seccion.code for seccion in MAIN_NAV]

    assert all(codigos)
    assert len(codigos) == len(set(codigos))


def test_el_recorte_por_permisos_conserva_el_codigo(gestor_centro):
    from apps.core.navigation import sections_for

    for seccion in sections_for(gestor_centro):
        assert seccion.code


def test_el_menu_se_pliega_por_seccion(client, gestor_centro):
    client.force_login(gestor_centro)

    contenido = client.get(reverse("core:home")).content.decode()

    # Cabecera pulsable y lista que controla, atadas por id. "General" la ve
    # cualquiera: no depende de permisos.
    assert 'data-menu-toggle="general"' in contenido
    assert 'aria-controls="menu-seccion-general"' in contenido
    assert 'id="menu-seccion-general"' in contenido
    # Y la seccion que este usuario si tiene.
    assert 'data-menu-toggle="administracion"' in contenido
    # Por defecto, todo desplegado.
    assert 'aria-expanded="false"' not in contenido
