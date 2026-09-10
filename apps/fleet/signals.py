"""Avisos que manda la flota al resto del sistema.

La flota no sabe que existen las reservas, y asi debe seguir: quien tiene que
reaccionar a una baja de vehiculo se suscribe aqui.
"""

from django.dispatch import Signal

#: Un vehiculo sale de flota. Argumentos: `vehicle`, `actor`.
vehicle_retired = Signal()
