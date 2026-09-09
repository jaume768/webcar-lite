"""CRUD de clientes, marca de conflictivo y documentos privados."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import PhysicalDeleteNotAllowed
from apps.customers.models import Customer, CustomerDocument

from .factories import CustomerFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def datos_cliente(**cambios):
    datos = {
        "first_name": "Maria",
        "last_name": "González",
        "birth_date": "1990-05-12",
        "nationality": "ES",
        "document_type": "dni",
        "document_number": "12345678Z",
        "document_expiry": "",
        "email": "maria@ejemplo.es",
        "phone": "600112233",
        "phone_alt": "",
        "address": "Carrer Major 1",
        "city": "Palma",
        "province": "Illes Balears",
        "postal_code": "07001",
        "country": "ES",
        "licence_number": "B-123456",
        "licence_country": "ES",
        "licence_issued_on": "2010-03-01",
        "licence_expiry": "",
        "notes": "",
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Alta y validacion de documento
# ---------------------------------------------------------------------------


def test_el_alta_crea_el_cliente(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("customers:customer_create"), datos_cliente(), headers=HTMX)
    cliente = Customer.objects.get(document_number="12345678Z")

    assert respuesta.status_code == 200
    assert "crud:guardado" in respuesta.headers["HX-Trigger"]
    assert cliente.full_name == "Maria González"


def test_un_dni_mal_formado_se_rechaza_con_mensaje_claro(client, gestor_maestros):
    """Criterio de aceptacion."""
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("customers:customer_create"),
        datos_cliente(document_number="12345678A"),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "deberia ser Z" in respuesta.content.decode()
    assert not Customer.objects.filter(document_number="12345678A").exists()


def test_un_pasaporte_extranjero_se_acepta(client, gestor_maestros):
    """Criterio de aceptacion: sin validacion de patron."""
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("customers:customer_create"),
        datos_cliente(document_type="passport", document_number="AB1234567"),
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert Customer.objects.filter(document_number="AB1234567").exists()


def test_no_se_repite_el_mismo_documento(client, gestor_maestros):
    CustomerFactory(document_type="dni", document_number="12345678Z")
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("customers:customer_create"), datos_cliente(), headers=HTMX)

    assert respuesta.status_code == 422
    assert "Ya hay un cliente con ese documento." in respuesta.content.decode()


def test_el_alta_guarda_la_oficina_activa(client, gestor_maestros, palma):
    """Trazabilidad: quien lo capto. No recorta quien puede atenderle."""
    client.force_login(gestor_maestros)

    client.post(reverse("customers:customer_create"), datos_cliente(), headers=HTMX)

    assert Customer.objects.get(document_number="12345678Z").office == palma


def test_un_cliente_de_otra_oficina_se_puede_atender(client, gestor_maestros, alcudia):
    """El cliente es de la empresa: en agosto alquila en otra oficina y punto."""
    ajeno = CustomerFactory(last_name="Deotraoficina", office=alcudia)
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("customers:customer_list")).content.decode()

    assert "Deotraoficina" in contenido
    assert client.get(reverse("customers:customer_detail", args=[ajeno.pk])).status_code == 200


# ---------------------------------------------------------------------------
# Edad y antiguedad de carnet
# ---------------------------------------------------------------------------


def test_la_edad_sale_de_la_fecha_de_nacimiento():
    from datetime import date

    from django.utils import timezone

    hoy = timezone.localdate()
    cliente = CustomerFactory(birth_date=date(hoy.year - 25, hoy.month, hoy.day))

    assert cliente.age == 25


def test_la_edad_no_cuenta_el_cumpleanos_que_aun_no_ha_llegado():
    from datetime import date, timedelta

    from django.utils import timezone

    manana = timezone.localdate() + timedelta(days=1)
    cliente = CustomerFactory(birth_date=date(manana.year - 25, manana.month, manana.day))

    assert cliente.age == 24


def test_sin_fecha_de_nacimiento_no_hay_edad():
    assert CustomerFactory(birth_date=None).age is None


def test_la_antiguedad_de_carnet_sale_de_la_expedicion():
    from datetime import date

    from django.utils import timezone

    hoy = timezone.localdate()
    cliente = CustomerFactory(licence_issued_on=date(hoy.year - 8, hoy.month, hoy.day))

    assert cliente.licence_years == 8


# ---------------------------------------------------------------------------
# Marca de cliente conflictivo
# ---------------------------------------------------------------------------


def test_marcar_a_un_cliente_exige_motivo(client, gestor_maestros):
    cliente = CustomerFactory()
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("customers:customer_blacklist", args=[cliente.pk]),
        {"is_blacklisted": "on", "blacklist_reason": "  "},
        headers=HTMX,
    )
    cliente.refresh_from_db()

    assert respuesta.status_code == 422
    assert cliente.is_blacklisted is False


def test_la_marca_se_guarda_con_su_motivo(client, gestor_maestros):
    cliente = CustomerFactory()
    client.force_login(gestor_maestros)

    client.post(
        reverse("customers:customer_blacklist", args=[cliente.pk]),
        {"is_blacklisted": "on", "blacklist_reason": "Devolvio el coche con danos sin declarar."},
        headers=HTMX,
    )
    cliente.refresh_from_db()

    assert cliente.is_blacklisted is True
    assert "danos" in cliente.blacklist_reason


def test_la_marca_se_ve_en_la_ficha(client, gestor_maestros):
    """Tiene que verse antes de alquilarle otra vez, no despues."""
    cliente = CustomerFactory(is_blacklisted=True, blacklist_reason="Impago de la franquicia.")
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("customers:customer_detail", args=[cliente.pk])).content.decode()

    assert "Cliente conflictivo" in contenido
    assert "Impago de la franquicia." in contenido


def test_un_cliente_marcado_sigue_en_el_selector_de_reservas():
    """La marca avisa, no prohibe: decide el responsable de la oficina."""
    from apps.customers.forms import CustomerChoiceField

    marcado = CustomerFactory(is_blacklisted=True, blacklist_reason="Aviso")

    campo = CustomerChoiceField()

    assert marcado in campo.queryset
    assert campo.label_from_instance(marcado).startswith("⚠")


def test_un_cliente_de_baja_no_esta_en_el_selector_de_reservas():
    from apps.customers.forms import CustomerChoiceField

    baja = CustomerFactory(is_active=False)

    assert baja not in CustomerChoiceField().queryset


# ---------------------------------------------------------------------------
# Documentos privados
# ---------------------------------------------------------------------------


def fichero_de_prueba(nombre="carnet.pdf"):
    return SimpleUploadedFile(
        nombre, b"%PDF-1.4 contenido de prueba", content_type="application/pdf"
    )


def test_subir_un_documento(client, gestor_maestros):
    cliente = CustomerFactory()
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("customers:document_create", args=[cliente.pk]),
        {"kind": "licence_front", "file": fichero_de_prueba(), "notes": ""},
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert cliente.documents.count() == 1


def test_un_documento_no_tiene_url_publica(settings):
    """Es dato personal: si tuviera URL, bastaria con adivinarla."""
    cliente = CustomerFactory()
    documento = CustomerDocument.objects.create(
        customer=cliente, kind="id_front", file=fichero_de_prueba()
    )

    with pytest.raises(ValueError):
        _ = documento.file.url

    # Y tampoco esta en MEDIA_ROOT, que es lo unico que sirve el servidor.
    assert str(settings.MEDIA_ROOT) not in documento.file.path


def test_la_descarga_exige_sesion_y_permiso(client, agente_palma):
    documento = CustomerDocument.objects.create(
        customer=CustomerFactory(), kind="id_front", file=fichero_de_prueba()
    )
    url = reverse("customers:document_download", args=[documento.pk])

    sin_sesion = client.get(url)
    client.force_login(agente_palma)
    sin_permiso = client.get(url)

    assert sin_sesion.status_code == 302
    assert sin_permiso.status_code == 403


def test_con_permiso_el_documento_se_descarga(client, gestor_maestros):
    documento = CustomerDocument.objects.create(
        customer=CustomerFactory(), kind="id_front", file=fichero_de_prueba()
    )
    client.force_login(gestor_maestros)

    respuesta = client.get(reverse("customers:document_download", args=[documento.pk]))

    assert respuesta.status_code == 200
    assert respuesta["Content-Disposition"].startswith("attachment")


def test_borrar_el_documento_borra_el_fichero(client, gestor_maestros):
    documento = CustomerDocument.objects.create(
        customer=CustomerFactory(), kind="id_front", file=fichero_de_prueba()
    )
    ruta = documento.file.path
    client.force_login(gestor_maestros)

    client.post(reverse("customers:document_delete", args=[documento.pk]), headers=HTMX)

    import os

    assert not CustomerDocument.objects.filter(pk=documento.pk).exists()
    assert not os.path.exists(ruta)


# ---------------------------------------------------------------------------
# Baja logica y permisos
# ---------------------------------------------------------------------------


def test_un_cliente_no_se_borra():
    cliente = CustomerFactory()

    with pytest.raises(PhysicalDeleteNotAllowed):
        cliente.delete()
    with pytest.raises(PhysicalDeleteNotAllowed):
        Customer.objects.filter(pk=cliente.pk).delete()


def test_dar_de_baja_y_reactivar(client, gestor_maestros):
    cliente = CustomerFactory()
    client.force_login(gestor_maestros)

    client.post(reverse("customers:customer_deactivate", args=[cliente.pk]), headers=HTMX)
    cliente.refresh_from_db()
    assert cliente.is_active is False

    client.post(reverse("customers:customer_activate", args=[cliente.pk]), headers=HTMX)
    cliente.refresh_from_db()
    assert cliente.is_active is True


ENDPOINTS = [
    ("customers:customer_list", "get", False),
    ("customers:customer_create", "get", False),
    ("customers:customer_create", "post", False),
    ("customers:customer_detail", "get", True),
    ("customers:customer_update", "get", True),
    ("customers:customer_update", "post", True),
    ("customers:customer_blacklist", "post", True),
    ("customers:customer_activate", "post", True),
    ("customers:customer_deactivate", "post", True),
    ("customers:document_create", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_palma, vista, metodo, con_objeto):
    cliente = CustomerFactory()
    client.force_login(agente_palma)

    url = reverse(vista, args=[cliente.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403
