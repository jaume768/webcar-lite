"""Dos empleados vendiendo a la vez el ultimo coche.

Es el escenario que justifica el cerrojo de ADR-001. Se ejecuta con
transacciones reales y varias veces seguidas: una condicion de carrera que solo
falla una vez de cada veinte es exactamente igual de grave, y mucho peor de
diagnosticar.
"""

import threading

import pytest
from django.db import connection

from apps.availability.services import NoAvailabilityError, reserve_capacity
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.reservations.models import Reservation

from .factories import en

#: Repeticiones. Con una sola pasada, una carrera perdida pasa desapercibida.
INTENTOS = 20


def _reservar_en_paralelo(hilos, category, office, inicio, fin):
    """Lanza `hilos` reservas simultaneas y devuelve como fue cada una."""
    barrera = threading.Barrier(hilos)
    resultados = []
    cerrojo = threading.Lock()

    def trabajo():
        try:
            # Todos esperan aqui: la carrera empieza a la vez para todos.
            barrera.wait(timeout=15)
            try:
                reserva = reserve_capacity(
                    category=category, pickup_office=office, start=inicio, end=fin
                )
            except NoAvailabilityError as exc:
                salida = ("rechazado", str(exc))
            except Exception as exc:  # el test reporta cualquier fallo inesperado
                salida = ("error", f"{type(exc).__name__}: {exc}")
            else:
                salida = ("aceptado", reserva.number)
        finally:
            connection.close()

        with cerrojo:
            resultados.append(salida)

    equipo = [threading.Thread(target=trabajo) for _ in range(hilos)]
    for hilo in equipo:
        hilo.start()
    for hilo in equipo:
        hilo.join(timeout=30)

    return resultados


@pytest.mark.django_db(transaction=True)
def test_dos_a_la_vez_por_el_ultimo_coche_solo_uno_lo_consigue():
    pool = OfficePoolFactory(code="baleares", name="Baleares")
    palma = OfficeFactory(code="palma", name="Palma", pool=pool)
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    VehicleFactory(plate="0001AAA", category=categoria, current_office=palma)

    inicio, fin = en(1), en(3)

    for intento in range(INTENTOS):
        Reservation.objects.all().delete()

        resultados = _reservar_en_paralelo(2, categoria, palma, inicio, fin)

        aceptadas = [r for r in resultados if r[0] == "aceptado"]
        rechazadas = [r for r in resultados if r[0] == "rechazado"]
        errores = [r for r in resultados if r[0] == "error"]

        assert not errores, f"intento {intento}: error inesperado {errores}"
        assert len(resultados) == 2, f"intento {intento}: algun hilo no termino"
        assert len(aceptadas) == 1, f"intento {intento}: se vendio dos veces {resultados}"
        assert len(rechazadas) == 1
        assert Reservation.objects.count() == 1, f"intento {intento}: sobreventa en base"


@pytest.mark.django_db(transaction=True)
def test_cinco_a_la_vez_con_dos_coches_entran_exactamente_dos():
    """La capacidad se respeta tambien cuando hay mas de un hueco en juego."""
    pool = OfficePoolFactory(code="baleares", name="Baleares")
    palma = OfficeFactory(code="palma", name="Palma", pool=pool)
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    for i in range(2):
        VehicleFactory(plate=f"200{i}BBB", category=categoria, current_office=palma)

    inicio, fin = en(1), en(3)

    for intento in range(5):
        Reservation.objects.all().delete()

        resultados = _reservar_en_paralelo(5, categoria, palma, inicio, fin)

        aceptadas = [r for r in resultados if r[0] == "aceptado"]
        assert not [r for r in resultados if r[0] == "error"], resultados
        assert len(aceptadas) == 2, f"intento {intento}: {resultados}"
        assert Reservation.objects.count() == 2
