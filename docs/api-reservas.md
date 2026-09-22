# API de reservas para la web del cliente (v1)

API JSON para que la web de la empresa consulte disponibilidad y precio, cree
reservas, las cancele y pida un enlace de pago (Stripe o Redsys). Las reservas
entran en el CRM como cualquier otra: canal `web`, estado **Pendiente**, precio
congelado con la tarifa web y disponibilidad revalidada dentro de una
transacción con el grupo de oficinas bloqueado.

**Se llama de servidor a servidor.** La clave vive en el backend de la web y no
se manda nunca al navegador. No hay CORS ni sesión.

## Alta de una web

```bash
make manage ARGS='sync_roles'                                    # crea el rol api_web (una vez)
make manage ARGS='api_client create "Web principal" --offices centro aeropuerto'
make manage ARGS='api_client rotate "Web principal"'             # nueva clave; la vieja deja de valer
make manage ARGS='api_client deactivate "Web principal"'
make manage ARGS='api_client list'
```

La clave (`rfk_…`) se enseña una sola vez; en BD solo queda su huella SHA-256.
Detrás de cada web hay un **usuario técnico** (rol `api_web`, sin contraseña
utilizable): sus oficinas son las que la web puede vender, y es él quien aparece
en el histórico y en la auditoría de cada reserva.

Para que una categoría se ofrezca en la web tiene que tener una **tarifa con
canal Web** para esas fechas. Sin ella, la categoría no aparece.

## Convenciones

- Autenticación: `Authorization: Bearer rfk_…`
- Fechas ISO 8601. Sin zona horaria se entienden en hora de España.
- Importes como cadena (`"137.94"`), en EUR. Nunca float.
- Límite: `BOOKING_API_RATE_LIMIT_PER_MINUTE` peticiones por minuto y web (429).
- Antelación mínima: `BOOKING_API_MIN_LEAD_MINUTES` (por defecto 120).
- Errores, siempre con la misma forma:

```json
{"error": {"code": "not_available", "message": "…", "fields": {}}}
```

| HTTP | code | Cuándo |
|------|------|--------|
| 400 | `invalid` | Datos mal formados; detalle por campo en `fields` (`customer.phone`, `extras.0.code`…) |
| 401 | `unauthorized` | Sin clave, clave falsa o web dada de baja |
| 403 | `forbidden` | El rol técnico no tiene el permiso |
| 404 | `not_found` | Reserva inexistente o creada por otra web |
| 409 | `not_available` | Ya no queda coche de esa categoría |
| 409 | `idempotency_conflict` | Misma `Idempotency-Key` con otra petición |
| 409 | `not_cancellable` / `not_payable` | Estado de la reserva que no lo admite |
| 422 | `unknown_office`, `unknown_category`, `unknown_extra`, `extra_quantity`, `too_soon`, `no_rate`, `payment_provider_disabled`, `payment_rejected`, `not_bookable` | Regla de negocio |
| 429 | `rate_limited` | Límite de peticiones |

`not_bookable` no explica el motivo a propósito (cliente marcado o de baja).

## Endpoints

### `GET /api/v1/offices/`
Oficinas que puede vender esta web.

### `GET /api/v1/extras/`
Extras activos con precio, tipo de cálculo (`once`, `per_day`, `per_reservation`) y cantidad máxima.

### `GET /api/v1/payment-providers/`
Pasarelas configuradas: `stripe`, `redsys`, las dos o ninguna.

### `GET /api/v1/availability/`
`?pickup_office=centro&return_office=aeropuerto&pickup_at=2026-10-05T10:00:00&return_at=2026-10-08T10:00:00`

Categorías con hueco y precio web, desglose por líneas incluido:

```json
{"results": [{"category": {"code": "eco", "name": "Económico", "seats": 5, …},
              "available_units": 6,
              "price": {"rental_days": 3, "total": "137.94", "lines": [...]}}]}
```

### `POST /api/v1/reservations/`
Cabecera **obligatoria** `Idempotency-Key` (hasta 80 caracteres, p. ej. el id del
carrito). Repetir la petición con la misma clave devuelve la reserva ya creada
(200) en vez de duplicarla (201 la primera vez).

```json
{
  "category": "eco",
  "pickup_office": "centro",
  "return_office": "aeropuerto",
  "pickup_at": "2026-10-05T10:00:00+02:00",
  "return_at": "2026-10-08T10:00:00+02:00",
  "extras": [{"code": "silla", "quantity": 1}],
  "notes": "Vuelo IB1234",
  "external_ref": "WEB-000123",
  "customer": {
    "first_name": "Lucía", "last_name": "Martín Pérez",
    "email": "lucia@example.com", "phone": "600111222",
    "document_type": "dni", "document_number": "12345678Z",
    "birth_date": "1990-05-01", "language": "es",
    "licence_number": "12345678Z", "address": "…", "city": "…", "postal_code": "…"
  },
  "payment": {"provider": "redsys", "purpose": "payment"}
}
```

- `customer`: obligatorios nombre, apellidos, correo, teléfono, tipo y número de
  documento (DNI/NIE con letra de control). Si el documento ya existe se usa esa
  ficha: la web solo **rellena** datos vacíos, nunca pisa los que hay.
- `payment` (opcional): `provider` `stripe` | `redsys`; `purpose` `payment`
  (por defecto, importe pendiente) o `advance` (con `amount`). La respuesta trae
  `payments[0].url`: redirige ahí al cliente. El cobro se apunta solo cuando la
  pasarela lo confirma con su notificación firmada.

Respuesta: la reserva (ver abajo).

### `GET /api/v1/reservations/{number}/`
Estado, fechas, desglose, `paid_amount`, `pending_amount`, `cancellation_fee` y
enlaces de pago. Solo reservas creadas por esta misma web.

### `POST /api/v1/reservations/{number}/cancel/`
Cancela por la máquina de estados (Pendiente o Confirmada), aplicando la política
de cancelación de la reserva. Libera el hueco.

### `POST /api/v1/reservations/{number}/payment-link/`
`{"provider": "stripe", "purpose": "advance", "amount": "50.00"}` → 201 con el enlace.
