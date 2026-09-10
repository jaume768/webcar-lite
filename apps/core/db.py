"""Piezas de SQL que Django no trae de serie.

Se usan dentro de campos generados, indices y constraints, asi que todas tienen
que ser inmutables para Postgres: nada que dependa de la hora actual, de la
zona horaria de la sesion ni de ninguna configuracion.

Ojo con lo que **no** vale aqui: `timestamptz + interval` es *estable*, no
inmutable (el resultado depende de la zona horaria de la sesion en los saltos
de hora), asi que una suma de fechas no puede ir dentro de una columna
generada. Si hace falta el resultado, se guarda en una columna normal y la
columna generada se construye a partir de ella.
"""

from django.contrib.postgres.fields import DateTimeRangeField
from django.db.models import Func


class TsTzRange(Func):
    """`tstzrange(inicio, fin, '[)')`.

    El limite superior queda fuera a proposito: una reserva que termina a las
    10:00 y otra que empieza a las 10:00 no se pisan.
    """

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()
