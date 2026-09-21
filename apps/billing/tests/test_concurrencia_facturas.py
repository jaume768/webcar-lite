"""Emisiones simultaneas con transacciones reales: ni numeros repetidos ni huecos."""

import threading

import pytest
from django.db import connection

from apps.billing.models import Invoice
from apps.billing.services import issue_invoice

from .conftest import finalizar

pytestmark = pytest.mark.django_db(transaction=True)


def test_dos_emisiones_a_la_vez_no_comparten_numero_ni_huella(reserva, centro, facturador):
    from apps.customers.tests.factories import CustomerFactory
    from apps.reservations.services import create_quick_reservation
    from apps.reservations.tests.factories import en

    otra = create_quick_reservation(
        category=reserva.category,
        pickup_office=centro,
        customer=CustomerFactory(),
        pickup_at=en(10),
        return_at=en(13),
    )
    reservas = [finalizar(reserva), finalizar(otra)]
    salida = threading.Barrier(len(reservas))
    errores = []

    def emitir(una):
        try:
            salida.wait()
            issue_invoice(reservation=una, actor=facturador)
        except Exception as exc:  # se comprueba abajo: no puede haber ninguno
            errores.append(exc)
        finally:
            connection.close()

    hilos = [threading.Thread(target=emitir, args=(una,)) for una in reservas]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    facturas = list(Invoice.objects.order_by("sequence"))
    assert errores == []
    assert [factura.sequence for factura in facturas] == [1, 2]
    # Una sola cadena: una empieza de cero y la otra apunta a ella. Sin el
    # bloqueo de la cadena, las dos leerian "" como anterior y se bifurcaria.
    primera = next(factura for factura in facturas if factura.hash_anterior == "")
    segunda = next(factura for factura in facturas if factura != primera)
    assert segunda.hash_anterior == primera.hash_actual
