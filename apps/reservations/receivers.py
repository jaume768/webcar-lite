"""Reacciones a lo que pasa en otras apps."""

from .services import mark_for_reassignment


def on_vehicle_retired(sender, vehicle, actor=None, **kwargs):
    """Un coche sale de flota: sus reservas futuras se quedan sin vehiculo.

    No se cancelan. El cliente tiene una reserva contra una categoria y hay que
    darle otro coche; perderla aqui seria perder una venta ya cerrada.
    """
    mark_for_reassignment(vehicle=vehicle, actor=actor)
