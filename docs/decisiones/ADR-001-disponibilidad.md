# ADR-001 · Cómo se calcula y se protege la disponibilidad

**Estado:** aceptado · **Fecha:** 2026-09-10 · **Ámbito:** `apps/availability`

## Contexto

El sistema tiene que responder con fiabilidad a una pregunta: *dada una categoría, una
oficina y unas fechas, ¿puedo aceptar otra reserva?* De esa respuesta cuelga todo lo demás,
y equivocarse tiene dos costes muy distintos:

- **Decir que sí cuando no hay coche** deja a un cliente en el mostrador sin vehículo. Es el
  fallo caro: hay que buscar un coche prestado, subir de categoría gratis o perder al cliente.
- **Decir que no cuando sí había** pierde una venta. Molesto, pero recuperable.

Ante la duda, el motor se equivoca por el lado conservador.

Dos hechos del dominio condicionan el diseño:

1. Una reserva puede existir **contra una categoría, sin vehículo asignado**. La
   disponibilidad no puede resolverse mirando coches concretos.
2. La flota se mueve entre oficinas de un mismo **grupo** (`OfficePool`). Un one-way de Palma
   al aeropuerto no cambia la capacidad del grupo.

## Decisión 1 · La capacidad se cuenta por categoría dentro de un grupo de oficinas

```
capacidad = vehículos activos de la categoría aparcados en el grupo
ocupación = vehículos bloqueados + reservas que ocupan flota
libre     = capacidad − ocupación
```

Una oficina **sin grupo** responde solo de su propia flota; su ámbito es ella misma.

### Qué ocupa y qué no

Consumen capacidad los estados `PENDIENTE`, `CONFIRMADA` y `EN_CURSO`. No consumen
`BORRADOR` (no compromete nada), `CANCELADA` y `NO_SHOW` (liberan el hueco) ni `FINALIZADA`
(el coche ya volvió). La constante viva es
`reservations.models.CAPACITY_CONSUMING_STATUSES`, y la usan tanto el motor como la
constraint de exclusión: **si cambia, hay que regenerar la constraint.**

### El estado del vehículo no decide la disponibilidad futura

`Vehicle.status` (taller, limpieza, alquilado) describe *ahora mismo*, y la disponibilidad se
pregunta sobre un periodo, casi siempre futuro. Lo que resta capacidad en un periodo son los
**bloqueos** (`VehicleBlock`), que sí tienen fechas. Un coche que hoy está en el taller pero
sin bloqueo la semana que viene cuenta como capacidad para la semana que viene.

Del recuento solo se excluyen las bajas: `is_active = False` o estado `BAJA`, que son
permanentes.

### Rotación entre alquileres

Entre dos alquileres del mismo coche hay limpieza y revisión: `VEHICLE_ROTATION_MINUTES`,
60 por defecto. Cada reserva **guarda el valor que se le aplicó** (`rotation_minutes`) en
lugar de leerlo de la configuración al consultar: subir el valor general no puede mover hacia
atrás la ocupación de lo ya vendido ni invalidar reservas aceptadas.

La ocupación real es `[recogida, devolución + rotación)`, materializada en `occupancy_period`.
Al alargar por el final **los dos lados** de la comparación (el periodo que se pide y el que
ya está guardado), queda garantizado un hueco de al menos la rotación entre dos alquileres
sin contarlo dos veces:

| A termina | B empieza | Rotación | Resultado |
| --- | --- | --- | --- |
| 10:00 | 10:00 | 0 min | libre (los rangos son `[)`) |
| 10:00 | 10:00 | 60 min | conflicto |
| 10:00 | 11:00 | 60 min | libre |
| 10:00 | 10:59 | 60 min | conflicto |

### One-way: dentro del grupo y entre grupos

| Caso | Efecto sobre la capacidad del grupo de origen |
| --- | --- |
| Recogida y devolución **en el mismo grupo** | Ocupa solo durante `[recogida, devolución + rotación)`. El coche vuelve al mismo sitio del que salió. |
| Devolución **en otro grupo** | Ocupa **desde la recogida y sin fecha de fin**, mientras la reserva siga viva. El coche no vuelve. |

El segundo caso es deliberadamente conservador. La alternativa —modelar dónde estará cada
coche en cada instante futuro— exige una línea temporal de posiciones de flota que no
tenemos y que se desincroniza en cuanto un traslado se hace sin registrarlo. Preferimos no
prometer un coche que no vamos a tener.

No hay doble penalización: cuando la reserva pasa a `FINALIZADA` deja de ocupar, y para
entonces la devolución ya ha actualizado la oficina del vehículo, de modo que el coche cuenta
en la capacidad de su nuevo grupo. El grupo de destino **no** suma el coche por adelantado:
hasta que llega físicamente, no es suyo.

**Fuera de alcance:** optimizar traslados entre oficinas y reasignar flota automáticamente.

## Decisión 2 · La concurrencia se serializa con una fila de cerrojo, no bloqueando vehículos

Contar y decidir no es atómico por sí solo. Dos empleados que venden a la vez el último coche
leen los dos "queda 1" y los dos venden. `reserve_capacity()` recuenta **dentro** de la misma
transacción que inserta, detrás de un cerrojo.

### Alternativa descartada: `select_for_update()` sobre los vehículos

Bloquear las filas de `Vehicle` de esa categoría y ese grupo antes de contar. Se descartó por
tres motivos:

1. **No cubre el caso de capacidad cero ni el de reservas sin vehículo.** Si la categoría no
   tiene coches en ese grupo no hay filas que bloquear, y el bloqueo no serializa nada. Como
   una reserva puede existir sin vehículo asignado, la unidad que compite no es el coche: es
   *un hueco de la categoría*.
2. **Bloquea trabajo ajeno.** Las filas de `Vehicle` las tocan el check-out (kilómetros), los
   traslados y los cambios de estado. Vender una reserva dejaría en espera operaciones de
   mostrador que no compiten por nada.
3. **El coste crece con la flota.** Cien coches de una categoría son cien filas bloqueadas
   para decidir sobre una reserva.

### Lo elegido: `availability.CategoryLock`

Una fila por `(categoría, ámbito)`, donde el ámbito es `pool:<id>` o `office:<id>`. Antes de
contar, `SELECT ... FOR UPDATE` sobre esa fila. Es exactamente el recurso en disputa: un
cerrojo, cero filas de dominio bloqueadas, y funciona igual con cero vehículos que con
doscientos.

El ámbito va como texto (`scope_key`) y no como dos claves ajenas anulables para que la
unicidad sea una restricción normal, sin la semántica de NULL de Postgres.

**Contrapartida asumida:** el cerrojo hay que acordarse de cogerlo. Por eso `reserve_capacity()`
y `update_reservation_period()` son el **único** camino para crear o reprogramar una reserva
que ocupe flota, y por eso existe la red de seguridad de la decisión 3.

## Decisión 3 · La base de datos como última red, no como validación principal

`Reservation` lleva una `ExclusionConstraint` con `btree_gist` sobre
`(occupancy_period, vehicle)`, con un `WHERE` parcial que deja fuera las reservas sin vehículo
y las que no ocupan flota.

Es una **red de seguridad**, no la validación principal: cuando salta, el usuario recibe un
`IntegrityError`, no un mensaje útil. El motor comprueba antes y explica por qué. La
constraint está para lo único que el código de aplicación no puede garantizar: que dos
transacciones simultáneas no asignen el mismo coche al mismo rato.

`occupancy_period` es una **columna generada** (`STORED`), no un campo que escriba la
aplicación. Así el rango que ven el índice y la constraint sale siempre de las fechas reales.

Un detalle de Postgres que condicionó el diseño: `timestamptz + interval` es **estable**, no
inmutable (depende de la zona horaria de la sesión en los saltos de hora), y una expresión no
inmutable no puede ir dentro de una columna generada ni de un índice. Por eso el fin de la
ocupación se materializa en su propia columna `occupancy_end`, que escribe `save()`, y la
columna generada solo empaqueta las dos fechas en un rango. Un `CheckConstraint` vigila que
`occupancy_end >= return_at` por si alguien escribe con un `UPDATE` a pelo.

## Decisión 4 · `exclude_reservation` es obligatorio al revalidar

Al cambiar las fechas de una reserva que ya existe hay que excluirla del recuento. Si no, se
cuenta a sí misma como ocupación y se bloquea sola: alargar una reserva sería imposible en
cuanto la categoría estuviera al límite. `update_reservation_period()` lo pasa siempre.

## Decisión 5 · El overbooking se autoriza, no se tropieza

`reserve_capacity(override=Override(user, reason))` acepta por encima de la capacidad si el
usuario tiene `availability.override_availability` **y** escribe un motivo. Sin motivo no hay
override, aunque sobre el permiso.

Queda registrado en la propia reserva (`overbooked`, `override_reason`, `override_by`) además
de en el log. Se guarda en la reserva y no solo en la auditoría porque quien la abre seis
meses después tiene que ver por qué se aceptó sin mirar en otro sitio.

## Consecuencias

- Toda creación o reprogramación de reservas pasa por `availability.services`. No hay
  `Reservation.objects.create()` en vistas.
- `CAPACITY_CONSUMING_STATUSES` es un punto único de verdad compartido por el motor y el
  esquema. Cambiarlo obliga a una migración.
- `get_available_categories()` hace una consulta por categoría. Con las decenas de categorías
  de una empresa de alquiler sobra; si algún día son cientos, se agrupa en una sola consulta.
- El caso one-way entre grupos es conservador y puede rechazar reservas que en la práctica
  serían servibles. Es la decisión correcta hoy; revisarla exige modelar la posición futura
  de la flota.

## Limitaciones conocidas

- Un vehículo bloqueado que además tuviera una reserva encima se cuenta una sola vez (como
  bloqueado). Un coche cuya reserva asignada esté fuera del grupo se cuenta de forma
  conservadora.
- La disponibilidad no reserva coche concreto: entre la venta y la entrega, quién se lleva
  qué matrícula lo decide la asignación, que revalida por su cuenta.
