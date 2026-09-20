# Motor de tarifas

Un sitio y solo uno donde se calcula un precio:

```python
from apps.pricing.dto import ExtraRequest, PriceQuoteInput
from apps.pricing.services import calculate_reservation_price

precio = calculate_reservation_price(
    PriceQuoteInput(
        category=categoria,
        pickup_office=centro,
        return_office=norte,
        pickup_at=salida,
        return_at=devolucion,
        extras=(ExtraRequest(silla, 1),),
        customer_age=21,
    )
)
precio.total          # Decimal("320.65")
precio.to_dict()      # serializable a JSON tal cual
```

El motor **no conoce `Reservation`** y no va a conocerla: recibe un DTO y
devuelve otro. Por eso se puede pedir un precio antes de que la reserva exista
(que es lo que hace el mostrador cuando preguntan "¿y una semana?") y se puede
probar entero sin cliente HTTP.

| Fichero | Que hay |
| --- | --- |
| `pricing/dto.py` | `PriceQuoteInput`, `PriceBreakdown`, `PriceLine` |
| `pricing/services/duration.py` | `rental_days()` |
| `pricing/services/rates.py` | temporada, tarifa, tramos y sus errores |
| `pricing/services/engine.py` | `calculate_reservation_price()` |

## Duracion: `rental_days()`

Periodos de 24 horas con **margen de cortesia** (59 minutos por defecto,
`RENTAL_COURTESY_MINUTES`). Si el exceso no llega al margen, no se cobra un dia
mas.

| Duracion real | Dias facturados |
| --- | --- |
| 2 horas | 1 (nunca se factura cero) |
| 24 h exactas | 1 |
| 3 dias + 30 min | 3 |
| 3 dias + 90 min | 4 |

Es la unica funcion del sistema que convierte fechas en dias. El precio, la
disponibilidad y el contrato leen todos de aqui.

## Temporada: manda la fecha de recogida

Las temporadas **pueden solaparse**: encima de "temporada alta" se pone
"Semana Santa" con mas prioridad, sin recortar la de debajo. Gana la de mayor
`priority`; si dos empatan, `AmbiguousSeason`.

Una reserva que cruza el cambio de temporada **se cobra entera a la temporada de
la fecha de recogida**, y el desglose lo dice en `warnings`. Es lo que espera el
cliente (le diste un precio al reservar) y lo que hace el sector; partir el
alquiler daria un precio que nadie sabe explicar en el mostrador.

## Tarifa: prioridad, luego especificidad, luego error

Se filtran las tarifas activas por categoria, oficina, canal, fecha de vigencia,
duracion y temporada. Despues se ordenan por:

1. `priority` (mayor gana);
2. **especificidad**, que es una puntuacion (`rates.specificity`): temporada
   +4, oficinas concretas +2, ventana de dias +1, una sola categoria +1.

Si dos tarifas siguen empatadas → **`AmbiguousRate`** con los codigos que
empatan. No se elige a ciegas: el precio de un alquiler no puede depender de en
que orden devuelva las filas Postgres.

Sin ninguna tarifa aplicable → **`NoRateAvailable`**. Nunca un precio 0: un cero
silencioso acaba en un contrato firmado a cero euros.

## Tramos: PLANO y PROGRESIVO

Con los tramos 1 / 2-3 / 4-7 / 8-14 / 15+ a 50/45/40/35/30:

| Dias | PLANO | PROGRESIVO |
| --- | --- | --- |
| 1 | 50 | 50 |
| 3 | 135 | 140 |
| 4 | 160 | 180 |
| 7 | 280 | 300 |
| **8** | **280** | 335 |
| 14 | 490 | 545 |
| **15** | **450** | 575 |

**PLANO** (por defecto, y lo estandar del sector): todos los dias al precio del
tramo en el que cae la duracion total.

> El escalon de 7→8 y de 14→15 hace que el total **baje** al alargar el
> alquiler. **Es correcto y es intencionado**: es el incentivo comercial a
> alquilar mas dias. Hay un test que lo fija (`test_el_escalon_de_siete_a_ocho_dias_baja_el_precio`)
> justamente para que nadie lo "arregle" dentro de seis meses.

**PROGRESIVO**: cada dia al precio de su propio tramo, como los tramos de la
luz. Aqui alargar nunca abarata. Se activa por tarifa con `tier_mode`.

## Suplementos

| Tipo | Cuando se aplica |
| --- | --- |
| `ONE_WAY` | `pickup_office.pool != return_office.pool` |
| `YOUNG_DRIVER` | `min_age <= customer_age <= max_age` |
| `AFTER_HOURS` | recogida o devolucion fuera de `hours_from`-`hours_to` |
| `AIRPORT` | recogida o devolucion en una de sus `offices` |

Dos oficinas **sin** pool son sitios distintos: `None == None` no significa
"misma flota". Devolver en la misma oficina nunca es one-way.

Un suplemento por dia (el tipico de conductor joven) se modela como
**porcentaje**: la base del alquiler ya es proporcional a los dias, asi que un
15% sube igual que un fijo diario y ademas acompana al precio del tramo.

Sin edad conocida no se cobra el suplemento joven: cobrar de mas es peor que
preguntar.

## Descuentos

Sin codigo se aplican solos cuando se cumplen sus condiciones. Con codigo hay
que teclearlo: nadie se lleva un descuento por accidente. Un codigo que no vale
no tumba el presupuesto, pero deja un aviso en `warnings`.

Van como lineas **negativas** sobre la base del alquiler y nunca la dejan por
debajo de cero. `discounts_total` viene en positivo: es "lo que se ha
descontado".

## Redondeo e impuestos

- `Decimal` de principio a fin. Ni un `float`.
- Se redondea **al cerrar cada linea**, con `ROUND_HALF_UP`: primero la base,
  luego el impuesto sobre esa base ya redondeada.
- Los totales son sumas de lineas ya redondeadas, nunca un calculo aparte.

De ahi salen dos invariantes que verifica un test de propiedad con 1.000
combinaciones aleatorias (`test_tax_property.py`):

```
taxable_base + tax_total == total
taxable_base == suma de las bases de las lineas
```

Cada linea guarda base, impuesto y total por separado, que es justo lo que
necesita la factura cuando llegue: el total de la reserva sera la suma de sus
lineas, nunca un numero suelto.

## Precio manual

`manual_override` fija el precio por dia a mano (el permiso lo comprobara la
vista que lo use, `reservations.change_reservation_price`). Se respeta, y queda
escrito en `warnings` para quien lea la reserva manana.

## Cambiar precios sin tocar codigo

Los tramos viven en la base de datos. Cambiar `price_per_day` de un `RateTier`
cambia el precio en la siguiente consulta, sin desplegar nada. Hay un test que
lo comprueba (`test_cambiar_los_tramos_en_la_base_de_datos_cambia_el_precio`).

## Las pantallas

| Pantalla | Ruta | Para que |
| --- | --- | --- |
| Tarifas | `/tarifas/` | alta, edicion con tramos, duplicar, activar |
| Temporadas | `/temporadas/` | fechas y prioridad |
| Suplementos | `/suplementos/` | one-way, joven, fuera de horario, aeropuerto |
| Descuentos | `/descuentos/` | automaticos y con codigo |
| Extras | `/extras/` | silla, GPS, segundo conductor |
| **Simulador** | `/simulador-de-precios/` | calcular con la configuracion real |
| **Conflictos** | `/tarifas/conflictos/` | dos tarifas que se pisan |

### Editor de tramos

Los tramos se editan **dentro** de la tarifa, en el mismo formulario y la misma
transaccion: una tarifa sin tramos no tiene precio, asi que guardarla a medias
es peor que no guardarla.

Mientras se teclea, el servidor devuelve el estado de la cobertura
(`pricing:tier_check`): el hueco del dia 4 se ve al momento, sin esperar a
pulsar Guardar. Ese aviso **no** es la validacion: la que manda es la del
guardado (`RateTierBaseFormSet.clean` y, otra vez, `save_rate`), y rechaza
tramos 1-3 + 5-7 aunque alguien mande el POST a mano.

### Simulador

Ensena el total **y por que sale ese total**: dias facturados, tarifa aplicada
con su codigo, tramo aplicado, temporada, y una linea por concepto con su base y
su impuesto. Si no hay tarifa, ensena el `NoRateAvailable` tal cual, que es
justo lo que veria el mostrador. No crea nada: es el motor, con datos reales, sin
reserva de por medio.

### Conflictos

`find_rate_conflicts()` recorre las parejas de tarifas activas y avisa de las que
empatan en prioridad **y** especificidad para la misma categoria, canal,
oficinas, fechas y duracion. Un empate aqui es un `AmbiguousRate` en el
mostrador con un cliente delante; mejor verlo antes.

### Duplicar

Copia tarifa, tramos, categorias y oficinas. La copia nace **desactivada** (que
nadie venda con una tarifa a medio repasar) y el modal de edicion se abre solo
para ajustar temporada y precios. Es como se monta la temporada alta a partir de
la baja.
