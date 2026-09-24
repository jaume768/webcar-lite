# RentFlow · CRM Rent a Car

Sistema de gestión para una empresa de alquiler de vehículos sin conductor en España.
Multi-oficina y pensado para el **uso real en mostrador**: reservas, entregas y devoluciones,
flota y taller, tarifas, cobros y pagos online, facturación preparada para Verifactu, contratos
en PDF, multas, partes a SES.Hospedajes, correos al cliente en su idioma y una API para que la
web de la empresa reserve directamente.

**Una instalación por cliente.** Cada empresa de alquiler tiene su propio despliegue, con su base
de datos, su dominio y su configuración. No es multi-tenant: una instalación sirve a una sola
empresa (con todas sus oficinas). Ver [Modelo de despliegue](#modelo-de-despliegue).

Django 5.2 · PostgreSQL 16 · Redis + Celery · HTMX + Alpine + Tailwind · WeasyPrint · Docker.

---

## Índice

- [Arranque](#arranque)
- [Qué hace](#qué-hace)
- [Módulos](#módulos)
- [Tareas programadas](#tareas-programadas)
- [Configuración](#configuración)
- [Comandos y servicios](#comandos-y-servicios)
- [Arquitectura](#arquitectura)
- [Acceso, permisos y oficinas](#acceso-permisos-y-oficinas)
- [Sistema de interfaz](#sistema-de-interfaz)
- [Tests y CI](#tests-y-ci)
- [Producción](#producción)
- [Estado y pendientes conocidos](#estado-y-pendientes-conocidos)

---

## Arranque

```bash
cp .env.example .env
make up          # construye y levanta todo; espera a /health/
make seed        # roles, 3 oficinas y el superusuario admin@localhost / admin (solo DEBUG)
make manage ARGS="seed_demo"   # opcional: empresa, flota, clientes y un mes de actividad
```

La aplicación queda en `http://localhost:${WEB_PORT}` (8000 por defecto) y el estado en `/health/`.
El acceso es privado: sin sesión, cualquier URL lleva a `/entrar/`.

Requisitos en la máquina: Docker y Docker Compose v2. Python, Postgres, Redis y Node viven dentro
de los contenedores. Si el 8000 o el 5432 están ocupados, cambia `WEB_PORT` o `POSTGRES_PORT` en
`.env` (solo afectan a los puertos publicados en el host).

---

## Qué hace

| Área | Resumen | Dónde |
| --- | --- | --- |
| Panel de mostrador | Entregas y devoluciones del día, estado de la flota, ocupación, avisos. Se refresca solo | `/` |
| Reservas | Alta rápida con precio en vivo, ficha con pestañas, máquina de estados, planning | `/reservas/`, `/planning/` |
| Entrega y devolución | Check-in con km, combustible y daños con fotos; check-out con cargos automáticos | Ficha de la reserva |
| Clientes | Ficha con validación DNI/NIE, documentos privados, idioma, lista negra | `/clientes/` |
| Flota | Categorías, vehículos, bloqueos de calendario | `/vehiculos/`, `/categorias/`, `/bloqueos/` |
| Mantenimiento y taller | Revisiones, aceite, ITV, neumáticos, frenos, averías, chapa; inmovilizaciones y vencimientos | `/mantenimiento/` |
| Tarifas | Tarifas por canal con tramos, temporadas, extras, suplementos, descuentos, simulador y conflictos | `/tarifas/` … |
| Cobros y caja | Anticipos, pagos, fianzas, devoluciones, reembolsos; arqueo diario por oficina | `/facturacion/cobros/`, `/caja/` |
| Pagos online | Enlace de pago al cliente por **Stripe o Redsys**; fianza como preautorización | `/facturacion/pagos-online/` |
| Facturación | Series con numeración sin huecos, facturas inmutables, rectificativas, facturas libres, Verifactu (huella encadenada y QR), PDF | `/facturacion/…` |
| Contratos | Contrato de alquiler en PDF con condiciones generales versionadas, en el idioma del cliente | Ficha de la reserva |
| Multas | Localiza la reserva por matrícula y hora, identifica al conductor y la repercute con gastos de gestión | `/multas/` |
| SES.Hospedajes | Parte del contrato (RD 933/2021): datos que faltan, XML, plazo, envío y reintentos | `/ses-hospedajes/` |
| Correos al cliente | Confirmación, recordatorio, contrato, devolución, factura, enlace de pago y pago recibido; en es/en/de/fr | `/correos/` |
| API de reservas web | La web de la empresa consulta disponibilidad y precio, reserva, cancela y pide enlace de pago | `/api/v1/…` |
| Informes y gestoría | Ingresos por coche, ocupación por mes y exportación CSV de facturas y cobros | `/informes/` |
| Captación | Formulario de contacto de la portada pública y seguimiento de los interesados | `/`, `/contactos/` |
| Auditoría | Quién hizo qué y cuándo, con IP; inmutable | `/auditoria/` |
| Configuración | Datos de la empresa y logo, correos automáticos, condiciones generales, políticas de factura | `/configuracion/`, `/politicas/` |
| Usuarios | Alta, roles, oficinas asignadas, baja lógica | `/usuarios/` |

---

## Módulos

### Panel de mostrador

Pantalla de inicio (`operations.dashboard`). Entregas y devoluciones de hoy (o del periodo
elegido), reservas sin coche asignado, flota por estado y ocupación, accesos directos a caja y
alta rápida. Se arma con un puñado de consultas contadas, sea cual sea el volumen, y se refresca
cada `DASHBOARD_REFRESH_SECONDS`.

### Reservas

- **Alta rápida** (`/reservas/rapida/`): categoría, oficinas, fechas, cliente (buscador o alta en
  el momento) y extras; el precio se recalcula mientras se teclea. Nace **Pendiente** y con el
  precio congelado: la reserva guarda el desglose, no la referencia a la tarifa.
- **Ficha** con cabecera fija y pestañas por HTMX: Resumen, Cliente, Vehículo, Extras, Precio,
  Cobros, Check-in, Check-out, Documentos e Historial (estados, precios, auditoría y correos
  juntos en una línea de tiempo).
- **Cambios** de fechas, categoría u oficina con vista previa del nuevo precio y revalidación de
  disponibilidad. Precio manual con permiso y motivo; cada cambio de precio queda registrado.
- **Conductores adicionales** con validación de carnet contra la reserva.
- **Estados** (`reservations/state_machine.py`), la única puerta para cambiarlos:

  ```
  BORRADOR -> PENDIENTE -> CONFIRMADA -> EN_CURSO -> FINALIZADA
                        -> CANCELADA
  CONFIRMADA -> NO_SHOW
  ```

  Cada salto declara su permiso, precondiciones (cliente, precio, vehículo, check-in…) y efectos
  (política de cancelación, liberar coche, marcar alquilado…).
- **Reserva facturada = reserva cerrada**: con factura en vigor no se mueven fechas, precio,
  extras, vehículo ni estado. Se corrige con rectificativa.
- **Planning** (`/planning/`): calendario por categoría y vehículo, con una fila para lo que aún
  no tiene coche. Dos consultas, sea cual sea el rango.
- **Numeración** sin huecos (`RESERVATION_NUMBER_FORMAT`, p. ej. `R{year}-{sequence:05d}`).

### Disponibilidad

Un problema de **capacidad por categoría dentro de un grupo de oficinas** (`OfficePool`): coches
de la categoría en el grupo, menos reservas solapadas (con o sin coche asignado), menos bloqueos y
taller. Incluye el tiempo de rotación entre alquileres (`VEHICLE_ROTATION_MINUTES`). El recuento y
la inserción van en la misma transacción tras un cerrojo del grupo (`select_for_update`), y una
`ExclusionConstraint` con `btree_gist` impide físicamente el doble uso de un vehículo asignado.
Forzar por encima de la capacidad (overbooking) exige permiso y motivo. Decisión documentada en
[`docs/decisiones/ADR-001-disponibilidad.md`](docs/decisiones/ADR-001-disponibilidad.md).

### Entrega y devolución

- **Check-in**: km, combustible, carnet y documento comprobados, daños preexistentes con zona,
  tipo, gravedad y fotos (almacén privado). Pone el coche en ALQUILADO y prepara el parte de
  SES.Hospedajes.
- **Firma del cliente**: el cliente firma con el dedo en la tablet dentro del mismo formulario de
  entrega. La firma viaja como PNG en base64, se valida en el servidor (`operations/signature.py`:
  que sea un PNG de verdad y que no pase de 256 KB) y se guarda en el acta con su hora. Si no se
  puede firmar en el momento, **el coche sale igual** y la entrega queda *pendiente de firma*, con
  un botón para recogerla después; firmar dos veces no se permite, porque entonces la firma no
  probaría nada. La firma sale impresa en el contrato.
- **Check-out**: km, combustible, daños nuevos y oficina de devolución. Calcula los cargos
  (`operations/charges.py`, funciones puras): kilómetros de más (`EXTRA_KM_PRICE`), combustible
  (`FUEL_PRICE_PER_LITER`), devolución tardía, limpieza, daños y cargos manuales. El coche vuelve
  a `VEHICLE_STATUS_AFTER_CHECKOUT` (limpieza o disponible).

### Clientes

DNI y NIE con letra de control; el pasaporte no se valida por patrón. Datos completos para SES
(soporte del documento, sexo, dirección), carnet con fecha de expedición (antigüedad para
tarifas) e **idioma del cliente**, que decide el de contrato, factura y correos. Documentos
escaneados fuera de `MEDIA_ROOT`, servidos por una vista con permisos. Búsqueda trigram sin
acentos ("gonzal" → "González" en < 100 ms con 50.000 clientes). Marca de cliente conflictivo con
motivo interno.

### Flota

- **Categorías**: la unidad que se vende y con la que se cuenta la disponibilidad.
- **Vehículos**: matrícula, oficina actual, km, depósito, ITV y seguro. El estado es derivado de la
  operativa (ALQUILADO lo ponen el check-in y el check-out); a mano solo los de `MANUAL_STATUSES`.
- **Bloqueos**: periodos en que un coche no se vende (taller, traslado, limpieza…). Postgres
  impide que dos bloqueos del mismo coche se solapen.

### Mantenimiento y taller

`fleet/maintenance.py`, en `/mantenimiento/`.

- Tipos: revisión, aceite y filtros, ITV, neumáticos, frenos, avería, chapa y pintura, otros.
- Flujo **Programado → En taller → Hecho** (o Anulado).
- **Inmovilización**: al entrar en taller se crea un bloqueo en el calendario (la disponibilidad
  lo resta) y las reservas que tenían ese coche lo sueltan y quedan marcadas para reasignar. No se
  cancela ninguna. Un coche alquilado no puede entrar en taller.
- Al salir: se quita el bloqueo, el coche vuelve a disponible, se apuntan km (nunca hacia atrás),
  coste, factura del taller y, si es una ITV, la nueva caducidad.
- **Vencimientos**: próximo por fecha (`next_due_date`) o por km (`next_due_km`), más ITV y seguro
  de la ficha. Avisa 30 días o 1.000 km antes, lo vencido primero.

### Tarifas

Un único sitio calcula un precio: `pricing.services.calculate_reservation_price()`. Recibe un
`PriceQuoteInput` y devuelve un `PriceBreakdown` línea a línea (base, IVA y total por línea).

- Tarifas por **canal** (mostrador, web, partner), categoría, oficina, temporada y días, con
  editor de tramos en modo **plano** (por defecto) o **progresivo**.
- Temporadas con prioridad, extras (único, por día, por reserva; con tope), suplementos (one-way,
  conductor joven, fuera de horario, aeropuerto) y descuentos por código.
- **Simulador** con la configuración real y pantalla de **conflictos** entre tarifas.
- `rental_days()` es la única función que convierte fechas en días (24 h + margen de cortesía
  `RENTAL_COURTESY_MINUTES`).
- Sin tarifa aplicable → `NoRateAvailable`; dos igual de aplicables → `AmbiguousRate`. Nunca un
  precio 0 ni una elección silenciosa.

Detalle en [`docs/motor-tarifas.md`](docs/motor-tarifas.md).

### Cobros y caja

Anticipo, pago, fianza, devolución de fianza, cargo adicional y reembolso, por medio de pago
(efectivo, tarjeta, datáfono, transferencia, PayPal…). Cobrar por encima del pendiente exige
permiso. **Arqueo de caja** por oficina y día en `/caja/`.

### Pagos online: Stripe y Redsys

`billing/online.py` y `billing/gateways/`. Las dos pasarelas conviven y se elige una por enlace;
solo se ofrece la que tiene credenciales.

1. Desde la ficha se crea un **enlace de pago** (anticipo, pago o fianza) y se envía por correo.
2. El cliente abre `/pago/<token>/` (en su idioma) y va a Stripe Checkout o al TPV de Redsys.
3. El cobro se apunta **solo** cuando llega la notificación firmada de la pasarela
   (`/pagos/stripe/webhook/`, `/pagos/redsys/notificacion/`). Las notificaciones repetidas no
   duplican el cobro.
4. La **fianza** va como preautorización: se libera o se cobra una parte desde
   `/facturacion/pagos-online/`.

Los enlaces caducan a las `ONLINE_PAYMENT_LINK_HOURS`. Stripe se usa por su API REST, sin SDK;
Redsys con firma HMAC-SHA256 y 3DES.

### Facturación

- **Series** configurables con formato de número propio; numeración correlativa **sin huecos**
  con `select_for_update` sobre la fila de la serie.
- La factura **copia** los datos fiscales de la empresa y del cliente al emitirse y es
  **inmutable**: no se edita ni se borra. Se corrige con **rectificativa** (R4), que libera la
  reserva para volver a facturarla.
- **Facturas libres** a un cliente, sin reserva detrás (p. ej. una multa).
- **Verifactu** (`billing/verifactu.py`): huella SHA-256 encadenada con la de la factura anterior,
  fecha de registro y QR. El envío a la AEAT todavía no está implementado.
- PDF con WeasyPrint, generado al pedirlo (no se guarda: lo impreso está copiado en la factura),
  en el idioma del cliente y con las **políticas** marcadas para salir en factura.
- `/facturacion/pendiente/` lista lo finalizado sin facturar.

### Contratos

`contracts/`: contrato de alquiler en PDF generado en segundo plano con la **versión vigente de las
condiciones generales** (versionadas en `/configuracion/`; cada contrato guarda la versión con la que
se generó). Sale en el idioma del cliente y se puede enviar por correo. Si la entrega está firmada,
la rúbrica del cliente se imprime sobre la línea de firma, con la fecha y la hora.

Los textos del contrato, de la factura y de los correos están traducidos a en, de y fr; las
condiciones generales no se traducen, porque son texto de la empresa y se imprimen tal como se
publicaron. `apps/core/tests/test_i18n_documentos.py` falla si una de esas cadenas se queda sin
traducir o si `makemessages` la marca como dudosa (`fuzzy`), que es como un contrato acababa
saliendo en español para un cliente inglés.

### Multas

`operations/fines.py`, en `/multas/`. Con la matrícula y el momento de la infracción localiza la
reserva (horas reales de entrega y devolución si existen) y al conductor. Flujo: Recibida →
Reserva localizada / Sin reserva → Conductor identificado → Facturada al cliente → Cerrada. La
repercusión lleva `FINE_ADMIN_FEE` de gestión.

### SES.Hospedajes (RD 933/2021)

`compliance/`, en `/ses-hospedajes/`.

- Al hacer el check-in se prepara el parte con los datos de ese momento: contrato, vehículo,
  arrendatario, conductores adicionales y medio de pago.
- `ses.missing_fields()` dice en lenguaje de mostrador qué falta (segundo apellido, soporte del
  DNI, medio de pago…) para completarlo **antes** de entregar.
- Se genera el XML, se fija el plazo (`SES_DEADLINE_HOURS` desde la entrega) y, si está completo,
  se encola el envío. Los fallos de red se reintentan; lo pendiente se revisa cada 30 minutos.
- Se guarda lo enviado y la respuesta del Ministerio (lote) como prueba.
- **Sin `SES_ENABLED=True` no se envía nada**: el parte queda validado y **simulado**, con su XML
  descargable para subirlo a mano en la sede. Nunca se da por enviado algo que no salió.

> Antes de activar el envío real hay que cotejar las etiquetas XML y los códigos de catálogo de
> `compliance/ses.py` con el XSD y las tablas vigentes del Ministerio del Interior.

### Correos al cliente

`notifications/`. Tipos: confirmación, recordatorio de recogida (con instrucciones), contrato,
devolución, factura, enlace de pago y pago recibido. Cada uno se activa o desactiva en la
configuración. Se encolan **al confirmar la transacción** (si la operación se deshace, el correo
no sale), se montan en el idioma del cliente y quedan registrados en `/correos/`, con reintento.
Salen por la API de Brevo si hay `BREVO_API_KEY`; si no, consola en desarrollo y SMTP en
producción.

### API de reservas para la web del cliente

`booking_api/`. API JSON **de servidor a servidor**: la web guarda su clave en su backend y
nunca la manda al navegador.

| Método | Ruta | Para qué |
| --- | --- | --- |
| GET | `/api/v1/offices/` | Oficinas que puede vender esa web |
| GET | `/api/v1/extras/` | Extras con precio |
| GET | `/api/v1/payment-providers/` | Pasarelas configuradas |
| GET | `/api/v1/availability/` | Categorías con hueco y precio web |
| POST | `/api/v1/reservations/` | Reservar (cabecera `Idempotency-Key` obligatoria) |
| GET | `/api/v1/reservations/{n}/` | Estado, desglose, pagado y pendiente |
| POST | `/api/v1/reservations/{n}/cancel/` | Cancelar por la máquina de estados |
| POST | `/api/v1/reservations/{n}/payment-link/` | Enlace de pago Stripe o Redsys |

- Una web = un `ApiClient` con clave `rfk_…` (en BD solo su huella) y un **usuario técnico** con
  rol `api_web` y sin contraseña. Sus oficinas limitan lo que la web vende, y es él quien firma
  las reservas en el historial y la auditoría.
- Las reservas entran por el mismo alta que el mostrador: canal **web**, estado **Pendiente**,
  precio de las tarifas del canal web y disponibilidad revalidada con cerrojo.
- Repetir la petición con la misma `Idempotency-Key` devuelve la reserva ya creada en vez de
  duplicarla. Cada web solo ve y cancela sus propias reservas. Hay límite de peticiones por minuto.
- Un cliente que ya existe (mismo documento) se reutiliza; la web solo rellena datos vacíos.

```bash
make manage ARGS='api_client create "Web principal" --offices centro aeropuerto'
make manage ARGS='api_client rotate "Web principal"'      # nueva clave, la vieja deja de valer
make manage ARGS='api_client deactivate "Web principal"'
make manage ARGS='api_client list'
```

Referencia completa, errores y ejemplos en [`docs/api-reservas.md`](docs/api-reservas.md).
Para que una categoría se ofrezca en la web hace falta una **tarifa con canal Web**.

### Informes y gestoría

`reports/`, en `/informes/`. No tiene modelos propios (solo el ancla del permiso
`reports.view_reports`): lee de reservas, flota y facturación.

- **Ingresos por coche**: alquileres, días e ingresos de cada vehículo en el periodo, y el €/día.
  Se imputa por **fecha de recogida** y se cuenta `grand_total` (alquiler más cargos de la
  devolución). Las reservas canceladas y los no-show no cuentan.
- **Ocupación por mes**: días alquilados sobre días de flota (vehículos activos × días del mes). Un
  alquiler a caballo de dos meses reparte sus días entre ambos.
- **Exportación para la gestoría**: CSV de facturas y de cobros del periodo, con `;` como separador,
  coma decimal y BOM, que es lo que Excel en español abre sin tocar nada.

El cálculo vive en funciones puras (`revenue_rows`, `occupancy_rows`) y se prueba sin base de datos.
Todo pasa por el scope de oficina: quien solo trabaja en una oficina no ve los números de otra.

### Captación desde la portada

La portada pública (`/`) ofrece WhatsApp y un formulario corto. El contacto se guarda **siempre**
como `core.Lead` y el aviso por correo es un extra: si `LEADS_NOTIFY_EMAIL` está sin configurar o el
envío falla, el contacto sigue guardado y se consulta en `/contactos/` (Administración > Contactos
web), con estado (nuevo, contactado, cliente, descartado) y notas internas. El formulario lleva un
campo trampa para robots.

### Auditoría

`auditlog.services.record()` se llama dentro de la misma transacción que el hecho: si la
operación se deshace, el apunte también. Se registran altas y cambios de reserva, cambios de
estado y de precio, asignación de vehículo, cobros, pagos online, facturas, cancelaciones,
check-in, check-out, mantenimiento, multas, SES, correos, accesos y **accesos fallidos**. Guarda
actor, IP y navegador. No se puede modificar ni borrar, tampoco en bloque. Consulta en
`/auditoria/`, con el scope de oficina aplicado.

### Idiomas

La interfaz está en español. Lo que recibe el cliente (contrato, factura, correos, página de
pago) sale en **español, inglés, alemán o francés** según el idioma de su ficha. Traducciones en
`locale/`; `make messages` las actualiza y compila.

### Configuración de la empresa

`/configuracion/`: razón social, CIF (validado), dirección, logo, nota registral, qué correos
automáticos salen, horas del recordatorio, instrucciones de recogida y **condiciones generales
versionadas**. `/politicas/`: textos que pueden salir al pie de la factura.

### Modo demostración

Con `DEMO_MODE=True` la portada ofrece entrar con un clic a una cuenta de prueba
(`demo@webcar.example` / `demo-webcar-2026`). `seed_demo` carga una empresa con tres oficinas,
veintiséis coches, doce clientes y una veintena de reservas **creadas con los mismos servicios que
usa el mostrador**: terminadas, coches fuera ahora, entregas de hoy con y sin coche, cobros a
medias y fianzas retenidas (`--reset` rehace solo las reservas). **En producción, `DEMO_MODE`
siempre en `False`**; apagado, `/demo/` responde 404.

---

## Tareas programadas

`celery beat` (servicio `beat`, uno solo: dos duplicarían cada envío):

| Tarea | Cuándo | Qué hace |
| --- | --- | --- |
| `notifications.tasks.send_pickup_reminders` | cada hora, :05 | Recordatorio de recogida `reminder_hours` antes |
| `compliance.tasks.retry_pending_ses` | cada 30 min | Rehace los partes incompletos y reenvía los listos |
| `billing.tasks.expire_payment_links` | cada hora, :20 | Caduca los enlaces de pago vencidos |

El `worker` ejecuta además, a demanda: PDFs de contrato, envíos a SES y correos.

---

## Configuración

Todo entra por variables de entorno (`django-environ`). **`.env.example` documenta cada una**; aquí
van agrupadas.

| Grupo | Variables |
| --- | --- |
| Django | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_LOG_FORMAT`, `DJANGO_LOG_LEVEL`, `DJANGO_DEBUG_TOOLBAR` |
| Base de datos | `DATABASE_URL`, `DATABASE_CONN_MAX_AGE`, `POSTGRES_*` |
| Redis / Celery | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_TASK_ALWAYS_EAGER` |
| Ficheros | `DJANGO_PRIVATE_MEDIA_ROOT`, `MAX_UPLOAD_SIZE_MB` |
| Reservas | `RESERVATION_NUMBER_FORMAT`, `RESERVATION_DEFAULT_DEPOSIT`, `RESERVATION_DEFAULT_FRANCHISE`, `VEHICLE_ROTATION_MINUTES`, `RENTAL_COURTESY_MINUTES`, `DEFAULT_TAX_RATE` |
| Devolución | `EXTRA_KM_PRICE`, `FUEL_PRICE_PER_LITER`, `DEFAULT_TANK_LITERS`, `VEHICLE_STATUS_AFTER_CHECKOUT` |
| Multas | `FINE_ADMIN_FEE` |
| Pagos online | `PUBLIC_BASE_URL`, `ONLINE_PAYMENT_LINK_HOURS`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `REDSYS_MERCHANT_CODE`, `REDSYS_TERMINAL`, `REDSYS_SECRET_KEY`, `REDSYS_TEST` |
| SES.Hospedajes | `SES_ENABLED`, `SES_ENDPOINT`, `SES_USER`, `SES_PASSWORD`, `SES_LANDLORD_CODE`, `SES_APPLICATION`, `SES_DEADLINE_HOURS` |
| API web | `BOOKING_API_MIN_LEAD_MINUTES`, `BOOKING_API_RATE_LIMIT_PER_MINUTE` |
| Correo | `BREVO_API_KEY`, `DEFAULT_FROM_EMAIL`, `EMAIL_REPLY_TO`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` |
| Acceso | `AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES`, `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD` |
| Interfaz | `DASHBOARD_REFRESH_SECONDS`, `DEMO_MODE`, `DEMO_EMAIL`, `DEMO_PASSWORD` |
| Portada y captación | `CONTACT_WHATSAPP`, `CONTACT_PHONE`, `CONTACT_EMAIL`, `LEADS_NOTIFY_EMAIL` |
| Prod | `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS` |

Pasarelas: **cada una se ofrece solo si tiene credenciales**. Webhook de Stripe en
`{PUBLIC_BASE_URL}/pagos/stripe/webhook/` (eventos `checkout.session.*`); notificación de Redsys
en `{PUBLIC_BASE_URL}/pagos/redsys/notificacion/`.

Settings: `config.settings.dev` (desarrollo y tests) y `config.settings.prod` (Gunicorn,
Whitenoise con manifest, HSTS, cookies seguras).

---

## Comandos y servicios

`make help` lista todo.

| Comando | Qué hace |
| --- | --- |
| `make up` / `make down` | Levanta todo (espera a `/health/`) / para conservando datos |
| `make logs` | Logs de todos los servicios |
| `make test` / `make coverage` | pytest contra Postgres real (`ARGS="-k api"` para filtrar) |
| `make lint` / `make format` | ruff check + format |
| `make migrate` / `make makemigrations` | Migraciones |
| `make seed` | Roles, oficinas y superusuario de desarrollo |
| `make sync_roles` | Aplica los roles de `accounts/roles.py` (`ARGS="--prune"` quita lo que sobra) |
| `make messages` | Actualiza y compila las traducciones (en, de, fr) |
| `make manage ARGS="…"` | Cualquier comando de Django: `seed_demo`, `api_client`… |
| `make shell` / `make bash` / `make dbshell` | Consolas de Django, contenedor y psql |
| `make clean` | Para todo y **borra la base de datos** |

| Servicio | Imagen | Para qué |
| --- | --- | --- |
| `web` | build local | Django (runserver en dev, Gunicorn en la imagen de prod) |
| `db` | `postgres:16` | Base de datos |
| `redis` | `redis:7-alpine` | Caché, límite de la API y broker de Celery |
| `worker` | build local | Celery: PDFs, SES.Hospedajes, correos |
| `beat` | build local | Tareas programadas |
| `tailwind` | `node:22` | Watcher de CSS y vendorizado de HTMX y Alpine (perfil `dev`) |

`GET /health/` comprueba Postgres y Redis: `200 {"status": "ok"}` o `503` con el detalle.

---

## Arquitectura

```
config/            settings (base/dev/prod), urls, celery, wsgi/asgi
assets/css/        fuente de Tailwind; la salida va a static/css/app.css
docs/              motor de tarifas, API de reservas, ADR, patrón CRUD
locale/            traducciones en, de, fr
apps/
  core/            modelos base, mixins, shell, componentes, panel, healthcheck, seeds
  accounts/        User, Role, permisos, scope de oficina, login con bloqueo
  offices/         Office, OfficePool (grupos de oficinas para capacidad y one-way)
  customers/       Customer, documentos privados, validación DNI/NIE
  fleet/           VehicleCategory, Vehicle, VehicleBlock, MaintenanceRecord
  pricing/         Rate, RateTier, Season, Extra, Supplement, Discount + motor de cálculo
  availability/    motor de disponibilidad (solo CategoryLock como modelo)
  reservations/    Reservation, extras, conductores, cargos, historial, máquina de estados, planning
  operations/      CheckIn, CheckOut, Damage, DamagePhoto, TrafficFine, cargos, panel
  billing/         Payment, OnlinePayment, InvoiceSeries, Invoice, InvoiceLine, Verifactu, pasarelas
  contracts/       Contract y PDF
  compliance/      SesSubmission: SES.Hospedajes
  notifications/   EmailLog, envío por Brevo o SMTP
  booking_api/     ApiClient, ApiReservation: API de reservas web
  reports/         informes de explotacion y exportacion para la gestoria (sin modelos)
  auditlog/        AuditLog
  settings_app/    CompanySettings, TermsVersion, Policy
```

Reglas que sigue todo el código (completas en [`CLAUDE.md`](CLAUDE.md)):

- **Lógica en servicios** (`services.py`); las vistas orquestan. Los motores de tarifas y
  disponibilidad son **puros**: se prueban sin cliente HTTP.
- **Dinero** en `DecimalField(10, 2)` y `Decimal` con `ROUND_HALF_UP`; base, impuesto y total
  **por línea**. Nunca float, tampoco en JSON (los importes viajan como cadena).
- **Fechas** en UTC en BD, `Europe/Madrid` en pantalla.
- **Nada de borrado físico en maestros**: `is_active` y `deactivate()`. `ActivableModel.delete()`
  lanza `PhysicalDeleteNotAllowed`. La única excepción es anular un bloqueo de calendario.
- **Toda modificación de reserva que toque fechas, categoría, oficina o vehículo revalida la
  disponibilidad** en una transacción con `select_for_update`.
- Los **permisos se validan en backend** siempre; ocultar un botón no es validar.

### Modelo de despliegue

Para el MVP y la fase piloto, RentFlow se instala **una vez por cliente**:

- Cada empresa tiene su propio stack (web, worker, beat, PostgreSQL, Redis), su `.env`, su dominio
  y su base de datos. Los datos de una empresa no conviven nunca con los de otra.
- Dentro de una instalación hay **una sola empresa** (`CompanySettings` es único) y varias
  oficinas; el scope por oficina separa lo que ve cada usuario, no a empresas distintas.
- Actualizar a un cliente es desplegar la nueva imagen y ejecutar `migrate` y `sync_roles` en su
  instalación. Las versiones se pueden escalonar cliente a cliente.

**Multi-tenant queda para más adelante**, cuando haya varios clientes en producción y compense
operar una sola plataforma. Hasta entonces no se añade ningún campo de empresa/tenant a los
modelos ni se comparten bases de datos entre clientes.

### Base de datos

PostgreSQL 16, **también en tests**. Nada de SQLite: el dominio usa `tstzrange`, columnas
generadas, `btree_gist` y constraints de exclusión, además de `pg_trgm` para la búsqueda. Las
extensiones se habilitan en `apps/core/migrations/`.

### Modelos base

| Mixin | Qué añade |
| --- | --- |
| `TimeStampedModel` | `created_at`, `updated_at` |
| `ActivableModel` | `is_active`, `.active()` / `.inactive()`, `deactivate()` en vez de borrar |
| `UserStampedModel` | `created_by`, `updated_by`, rellenados solos |

`UserStampedModel` y la auditoría toman el usuario de un `ContextVar` publicado por
`core.middleware.CurrentUserMiddleware`, así que funcionan igual en vistas, comandos y tareas
(`with current_user(usuario): ...`).

### Patrón CRUD

Listado con `django-filter`, formulario en modal por HTMX, validación en servidor (**422** con los
errores) y baja lógica. Documentado en [`docs/patrones/crud.md`](docs/patrones/crud.md); piezas en
`apps/core/crud.py`.

---

## Acceso, permisos y oficinas

- **Privado de principio a fin**: `LoginRequiredMiddleware` obliga a tener sesión y lo público se
  marca una a una con `@login_not_required` (login, recuperar contraseña, `/health/`, página de
  pago, webhooks de pasarela y la API, que tiene su propia autenticación por clave). No hay alta
  pública: `/registro/`, `/signup/` y compañía responden **410 Gone**.
- **Login por correo**. Bloqueo por intentos fallidos con `django-axes` por IP + usuario
  (`AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES`); al superar el límite, **429**. Los fallos
  quedan en la auditoría.
- **Roles** declarados en `apps/accounts/roles.py` y aplicados con `sync_roles`: `consulta`,
  `mostrador`, `responsable`, `administracion` y `api_web` (usuario técnico de la API, que no se
  ofrece en el formulario de usuarios).

  ```
  permisos efectivos = los del rol + los asignados a mano + los del grupo
  ```

- **Permisos propios**: `accounts.manage_users`, `settings_app.access_settings`,
  `pricing.manage_rates`, `billing.view_billing`, `billing.add_payment`,
  `billing.allow_overpayment`, `billing.add_invoice`, `billing.rectify_invoice`,
  `billing.add_onlinepayment`, `reservations.change_reservation_price`,
  `reservations.cancel_reservation`, `availability.override_availability`…
- **Aislamiento por oficina**: toda consulta operativa pasa por `for_user(user)` (superusuario:
  todo; el resto: sus oficinas). Un objeto de otra oficina da **404, no 403**, para no confirmar
  que existe. En formularios, `OfficeScopedFormMixin` recorta el campo de oficina: un POST con otra
  oficina falla la validación. El selector de oficina activa contrasta siempre con las oficinas
  del usuario.
- **Panel de usuarios** en `/usuarios/`: un usuario nunca se borra y estrena su contraseña con el
  enlace de recuperación. El admin de Django queda en `/admin-interno/` solo como herramienta de
  soporte (`is_staff`).

---

## Sistema de interfaz

Sin SPA: plantillas de Django con HTMX y Alpine, CSS con Tailwind. El servicio `tailwind` compila
`assets/css/input.css` y copia HTMX y Alpine a `static/js/`: nada se sirve desde un CDN. Catálogo
vivo de componentes en `/ui-kit/` (menú Administración → Componentes).

- `base.html` monta barra superior, navegación lateral (`apps/core/navigation.py`, filtrada por
  permisos), selector de oficina activa, migas de pan, avisos y hueco de modales.
- **El menú lateral se pliega por secciones**: cada cabecera (Operativa, Flota, Facturación…) es un
  botón que oculta o muestra sus opciones. Por defecto está todo desplegado y la decisión se guarda
  en el navegador de cada persona (`localStorage`, clave `rentflow:menu-plegado`), así que sobrevive
  a cerrar la pestaña. Lo que se guarda es el `code` de la sección, no su etiqueta, que se traduce.
  El estado se aplica con un script en el propio menú para que nada dé un salto al cargar, y sin
  JavaScript el menú se ve entero.
- Componentes en `ui/`: tabla con búsqueda, filtros y paginación por HTMX, modal, confirmación
  destructiva, select con búsqueda en servidor, campos, fechas, badges de estado, estado vacío.
- Las tablas usan `django-template-partials`: la misma URL devuelve la página o solo el fragmento
  `#resultados` según la cabecera `HX-Request`.
- Avisos por `messages`, por `core.htmx.trigger_toast()` (cabecera `HX-Trigger`) o por un fallo de
  HTMX (403, 500, red), que `static/js/app.js` convierte en aviso. Páginas 403/404/500 propias.

---

## Tests y CI

```bash
make test
make test ARGS="apps/booking_api"
make test ARGS="-m slow"      # tests de volumen
make coverage
```

pytest + pytest-django + factory-boy contra **Postgres real**. Cobertura obligatoria en
`pricing`, `availability`, `reservations` y `billing`. Los motores se prueban sin base de datos
donde se puede, y la concurrencia con transacciones reales (`django_db(transaction=True)`): dos
peticiones a la vez no venden el último coche dos veces, ni siquiera por la API. Las pasarelas y
SES se prueban con respuestas simuladas; ningún test sale a internet.

`.github/workflows/ci.yml` en cada push y pull request: `ruff check` y `ruff format --check`,
`manage.py check`, `makemigrations --check --dry-run` y `pytest` con cobertura contra servicios
Postgres 16 y Redis 7.

---

## Producción

La imagen `runtime` (Dockerfile multi-etapa: assets con Node → dependencias → runtime) arranca
Gunicorn con `config.settings.prod`, sirve estáticos con Whitenoise y hace `collectstatic` en el
build.

Cada cliente es un despliegue independiente (ver [Modelo de despliegue](#modelo-de-despliegue)):
esta lista se repasa en cada instalación nueva.

Antes de desplegar:

- [ ] `DJANGO_SECRET_KEY` propia, `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` explícitos.
- [ ] `DEMO_MODE=False`.
- [ ] `PUBLIC_BASE_URL` con el dominio real (enlaces de pago y notificaciones de pasarela).
- [ ] Credenciales de Stripe y/o Redsys; `REDSYS_TEST=False`; webhook de Stripe dado de alta.
- [ ] Correo: `BREVO_API_KEY` o SMTP, y `DEFAULT_FROM_EMAIL`.
- [ ] SES.Hospedajes: código de arrendador y credenciales; `SES_ENABLED=True` solo tras cotejar el XML.
- [ ] Datos de la empresa, series de factura y condiciones generales en `/configuracion/`.
- [ ] `sync_roles` tras cada despliegue que cambie roles; un único `beat`.
- [ ] Tarifas con canal Web si se usa la API de reservas, y una clave por web (`api_client create`).

### Servidor con un Caddy ya instalado

`docker-compose.prod.yml` sirve para un servidor donde otro proyecto ya ocupa 80/443 con Caddy
(la demo pública, `rentflow.websjfs.com`). La web se une a la red de ese Caddy; base de datos y
Redis no publican puertos.

```bash
git clone https://github.com/jaume768/webcar-lite.git /srv/rentflow && cd /srv/rentflow
cp deploy/env.prod.example .env && chmod 600 .env    # secretos, dominio y PROXY_NETWORK
docker compose -f docker-compose.prod.yml up -d --build
cat deploy/rentflow.caddy >> <Caddyfile del Caddy existente>   # y caddy validate + reload
docker compose -f docker-compose.prod.yml exec rentflow-web python manage.py seed_demo   # solo demo
docker compose -f docker-compose.prod.yml exec rentflow-web python manage.py createsuperuser
```

Para actualizar: `git pull && docker compose -f docker-compose.prod.yml up -d --build`.

---

## Estado y pendientes conocidos

- **SES.Hospedajes**: preparación, validación, XML y envío implementados; falta cotejar etiquetas
  y códigos con el XSD oficial antes de activar el envío real.
- **Verifactu**: huella encadenada y QR desde el principio; el envío a la AEAT no está hecho.
- **Menú lateral**: Mantenimiento (`/mantenimiento/`), Multas (`/multas/`), Pagos online
  (`/facturacion/pagos-online/`), SES.Hospedajes (`/ses-hospedajes/`), Auditoría (`/auditoria/`)
  y Correos (`/correos/`) funcionan, pero todavía no tienen entrada en la navegación. Informes
  (`/informes/`) y Contactos web (`/contactos/`) sí la tienen.
- **Test en rojo**: `test_el_historial_junta_las_fuentes`. El contrato en el idioma del cliente ya
  está arreglado (eran traducciones marcadas como dudosas) y tiene test de regresión.
- **Fuera de alcance en v1**: portal público de reservas dentro del CRM (la web usa la API),
  OTAs y brokers, app móvil, firma biométrica y multi-empresa. Multi-tenant está previsto a largo
  plazo; en v1 cada cliente tiene su propia instalación.
