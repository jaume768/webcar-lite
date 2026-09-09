"""Base comun de los servicios de dominio."""


class ServiceError(Exception):
    """Una regla de negocio impide la operacion.

    No es un fallo del sistema: es un "no se puede, y por esto". Las vistas la
    convierten en un aviso legible para quien esta en el mostrador, nunca en un
    500. Cada app hereda la suya (`OfficeServiceError`, ...) para poder
    capturarla por separado cuando haga falta.
    """
