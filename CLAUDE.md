# CRM Rent a Car

Sistema de gestión para empresa de alquiler de vehículos sin conductor en España.
Uso real en mostrador, no demo. Multi-oficina.

## Stack

- Python 3.12, Django 5.x
- PostgreSQL 16 (obligatorio: usamos rangos y constraints de exclusión)
- Docker + Docker Compose
- Frontend: plantillas Django + HTMX + Alpine.js + Tailwind. NO hay SPA. NO hay API REST pública.
- Redis + Celery para tareas asíncronas (PDFs, envíos a SES.Hospedajes, emails)
- WeasyPrint para PDFs
- pytest + pytest-django + factory-boy
- ruff (lint + format), django-environ, structlog

## Reglas de arquitectura

1. **Lógica de negocio en servicios, nunca en vistas ni en modelos gordos.**
   Cada app tiene `services.py` (o paquete `services/`). Las vistas orquestan, no calculan.
2. **Los motores de tarifas y disponibilidad son puros**: reciben datos, devuelven resultado,
   no tocan la request ni la sesión. Se pueden testear sin cliente HTTP.
3. **Nada de borrado físico en maestros.** Campo `is_active`. Un vehículo dado de baja sigue
   apareciendo en reservas históricas.
4. **Los permisos se validan en backend siempre.** Ocultar un botón no es validar.
5. **Toda query de datos operativos pasa por un queryset con scope de oficina.**
   Si un usuario solo tiene Palma, no puede leer ni escribir nada de Alcúdia, ni por URL ni por form.
6. No crear apps nuevas sin justificarlo. Estructura fijada abajo.
7. Nada de `TODO` ni funciones vacías. Si algo queda fuera de alcance, se dice en el resumen final.

## Estructura de apps

```
config/            settings (base/dev/prod), urls, celery, wsgi/asgi
apps/
  core/            modelos base, mixins, utils, templatetags, plantillas base
  accounts/        User, Role, permisos, scope de oficina
  offices/         Office
  customers/       Customer, Driver, documentos
  fleet/           VehicleCategory, Vehicle, VehicleBlock, estados
  pricing/         Rate, RateTier, Season, Extra, motor de cálculo
  availability/    motor de disponibilidad (sin modelos propios salvo locks)
  reservations/    Reservation, ReservationExtra, ReservationDriver, máquina de estados
  operations/      CheckIn, CheckOut, Damage, cargos por devolución
  billing/         Payment, InvoiceSeries, Invoice, InvoiceLine
  contracts/       generación de contrato PDF
  compliance/      SES.Hospedajes (RD 933/2021)
  auditlog/        AuditLog
  settings_app/    CompanySettings y configuración editable
```

## Reglas de dominio invariables

### Dinero
- `DecimalField(max_digits=10, decimal_places=2)`. **Nunca float.**
- Cálculos con `decimal.Decimal` y `ROUND_HALF_UP`.
- Se guarda base imponible, impuesto y total por línea. El total de la reserva es la suma
  de líneas, nunca un número suelto recalculado a mano.
- Moneda EUR, configurable pero no multi-divisa en v1.

### Fechas
- `USE_TZ = True`, `TIME_ZONE = "Europe/Madrid"`. Todo en UTC en BD.
- Los días de alquiler se calculan por periodos de 24h con margen de cortesía configurable
  (por defecto 59 minutos). 3 días y 30 minutos = 4 días si el margen es 59 min... NO:
  = 3 días si el exceso es menor que el margen, 4 si lo supera. Definido en
  `pricing.services.rental_days()`. Un único sitio.

### Estados de reserva
```
BORRADOR -> PENDIENTE -> CONFIRMADA -> EN_CURSO -> FINALIZADA
                      -> CANCELADA
CONFIRMADA -> NO_SHOW
```
Transiciones permitidas declaradas en `reservations/state_machine.py`. Cualquier cambio de
estado pasa por ahí. Nunca `reservation.status = X; reservation.save()` en una vista.

### Disponibilidad
- Una reserva puede existir contra una **categoría** sin vehículo asignado.
- La disponibilidad es un problema de **capacidad**: nº de vehículos de la categoría en la
  oficina/pool, menos reservas solapadas (asignadas o no), menos bloqueos, menos taller.
- El solapamiento se calcula con `tstzrange` de PostgreSQL.
- Cuando hay vehículo asignado, una `ExclusionConstraint` con `btree_gist` impide físicamente
  el doble uso del mismo vehículo. Esto es una red de seguridad, no la validación principal.
- Toda creación o modificación de reserva que toque fechas, categoría, oficina o vehículo
  **revalida disponibilidad dentro de una transacción con `select_for_update`.**

### Facturas
- Una factura emitida es **inmutable**. No se edita, no se borra. Se corrige con rectificativa.
- Numeración correlativa por serie, sin huecos, obtenida con `select_for_update` sobre la
  fila de la serie dentro de la transacción que crea la factura.
- La factura **copia** los datos fiscales del cliente y de la empresa en el momento de emitir.
  Si el cliente cambia de dirección mañana, la factura de ayer no cambia.
- Preparada para Verifactu: campos `hash_actual`, `hash_anterior`, `qr_data`, `fecha_registro`.
  El encadenado se implementa desde el principio aunque el envío a AEAT llegue después.

### Auditoría
Se registran: creación/modificación de reserva, cambios de estado, cambios de precio manual,
asignación de vehículo, cobros, facturas, cancelaciones, check-in, check-out, login fallido.

## Testing

- `pytest`. Cobertura obligatoria en `pricing`, `availability`, `reservations`, `billing`.
- Los motores se testean sin base de datos donde sea posible.
- Concurrencia: tests con transacciones reales (`TransactionTestCase` / `django_db(transaction=True)`).
- Cada bug corregido lleva su test de regresión.

## Convenciones

- Nombres de modelos y campos en **inglés**. Interfaz de usuario en **español**.
- i18n activado desde el día 1 (`gettext`), aunque solo haya `es` al principio.
- Migraciones: una por prompt, nombre descriptivo. Nunca editar migraciones ya aplicadas.
- Commits en imperativo y en español.

## Fuera de alcance en v1 (no lo implementes aunque parezca útil)

- Portal web público de reservas para clientes finales.
- Integración con OTAs / brokers.
- App móvil.
- Firma digital biométrica.
- Multi-empresa / multi-tenant.