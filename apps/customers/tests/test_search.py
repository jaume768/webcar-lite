"""Buscador de clientes: acentos, coincidencias parciales y volumen."""

import time

import pytest
from django.db import connection

from apps.customers.models import Customer

from .factories import CustomerFactory, dni_valido

pytestmark = pytest.mark.django_db


def test_con_acentos_y_sin_acentos_devuelve_lo_mismo():
    """Requisito: en mostrador nadie escribe la tilde con prisa."""
    gonzalez = CustomerFactory(first_name="Maria", last_name="González")
    CustomerFactory(first_name="Luis", last_name="Ramirez")

    for termino in ("gonzalez", "González", "GONZALEZ", "gonzález"):
        assert list(Customer.objects.search(termino)) == [gonzalez], termino


def test_encuentra_por_un_trozo_del_apellido():
    """Criterio de aceptacion: 'gonzal' encuentra 'González'."""
    gonzalez = CustomerFactory(first_name="Maria", last_name="González")

    assert gonzalez in Customer.objects.search("gonzal")


def test_busca_tambien_por_documento_telefono_y_correo():
    cliente = CustomerFactory(
        first_name="Ana",
        last_name="Pérez",
        document_number="12345678Z",
        phone="600112233",
        email="ana.perez@ejemplo.es",
    )

    assert cliente in Customer.objects.search("12345678")
    assert cliente in Customer.objects.search("600112")
    assert cliente in Customer.objects.search("ana.perez")


def test_encuentra_por_nombre_y_apellido_juntos():
    cliente = CustomerFactory(first_name="Maria", last_name="González")

    assert cliente in Customer.objects.search("maria gonzalez")


def test_un_termino_vacio_no_filtra():
    CustomerFactory()
    CustomerFactory()

    assert Customer.objects.search("   ").count() == 2


def test_el_texto_de_busqueda_se_actualiza_al_editar():
    """Si el buscador se quedara con el apellido antiguo, no encontraria nada."""
    cliente = CustomerFactory(last_name="González")

    cliente.last_name = "Fernández"
    cliente.save()

    assert Customer.objects.search("fernandez").count() == 1
    assert Customer.objects.search("gonzalez").count() == 0


def test_un_save_parcial_tampoco_deja_el_buscador_antiguo():
    cliente = CustomerFactory(last_name="González")

    cliente.last_name = "Fernández"
    cliente.save(update_fields=["last_name"])

    assert Customer.objects.search("fernandez").count() == 1


def test_el_indice_trigram_existe_en_la_base_de_datos():
    """Sin el indice la busqueda funciona igual... hasta que hay volumen."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'customers_busqueda_trgm'"
        )
        definicion = cursor.fetchone()

    assert definicion is not None, "falta el indice de busqueda de clientes"
    assert "gin" in definicion[0].lower()
    assert "gin_trgm_ops" in definicion[0]


@pytest.mark.slow
def test_la_busqueda_es_rapida_con_cincuenta_mil_clientes():
    """Criterio de aceptacion: 'gonzal' encuentra 'González' en menos de 100 ms.

    Se ejecuta con `pytest -m slow` (crear 50.000 filas tarda demasiado para el
    ciclo normal). Mide la consulta, que es lo que se pretende acotar.
    """
    from apps.core.text import normalizar

    # Numeracion aparte de la de las fabricas: si se cruzaran, el documento
    # duplicado haria fallar el alta y no la busqueda, que es lo que se mide.
    def relleno(i: int) -> Customer:
        documento = dni_valido(90_000_000 + i)
        return Customer(
            first_name=f"Nombre{i}",
            last_name=f"Apellido{i}",
            document_number=documento,
            phone=f"6{i:08d}",
            search_text=normalizar(f"Nombre{i} Apellido{i} {documento} 6{i:08d}"),
        )

    lote = [relleno(i) for i in range(50_000)]
    Customer.objects.bulk_create(lote, batch_size=5_000)
    CustomerFactory(first_name="Maria", last_name="González")

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE customers_customer")

    inicio = time.perf_counter()
    encontrados = list(Customer.objects.search("gonzal")[:25])
    transcurrido = time.perf_counter() - inicio

    assert len(encontrados) == 1
    assert transcurrido < 0.1, f"la busqueda tardo {transcurrido * 1000:.0f} ms"
