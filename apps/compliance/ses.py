"""Parte de SES.Hospedajes (RD 933/2021) de un contrato de alquiler de vehiculo.

Funciones puras: de la reserva salen tres cosas, y ninguna toca la base de
datos ni la red.

1. `build_payload`: los datos que pide el Anexo del RD para el alquiler de
   vehiculos (contrato, vehiculo, arrendatario/conductores y pago), en un dict.
2. `missing_fields`: lo que falta para poder comunicarlo. Es lo que se ensena en
   mostrador para completarlo **antes** de entregar el coche.
3. `to_xml`: el documento que se envia (o se sube a mano a la sede).

IMPORTANTE: los nombres de las etiquetas XML y los codigos de catalogo
(`TIPO_DOCUMENTO`, `TIPO_PAGO`, `SEXO`) estan todos aqui a proposito. Antes de
activar el envio real (`SES_ENABLED=True`) hay que cotejarlos con el XSD y las
tablas de codigos vigentes que publica el Ministerio del Interior para el
servicio de comunicaciones de SES.Hospedajes: si cambian, se cambian aqui y en
ningun otro sitio.
"""

from datetime import date, datetime
from xml.etree import ElementTree as ET

from django.utils import timezone

#: Tipo de comunicacion: contrato de alquiler de vehiculo.
TIPO_COMUNICACION = "AV"

TIPO_DOCUMENTO = {"dni": "NIF", "nie": "NIE", "passport": "PAS"}
SEXO = {"male": "H", "female": "M", "other": "O"}
TIPO_PAGO = {
    "cash": "EFECT",
    "card": "TARJT",
    "dataphone": "TARJT",
    "transfer": "TRANS",
    "paypal": "PLATF",
    "other": "OTRO",
}

#: Documentos con numero de soporte: el pasaporte no lo tiene.
CON_SOPORTE = ("dni", "nie")


def _fecha(valor) -> str:
    return valor.isoformat() if isinstance(valor, date) and valor else ""


def _momento(valor: datetime | None) -> str:
    if valor is None:
        return ""
    return timezone.localtime(valor).replace(microsecond=0).isoformat()


def _apellidos(apellidos: str) -> tuple[str, str]:
    """Primer y segundo apellido. En Espana van en un solo campo: se parte por el primer espacio.

    Un apellido compuesto ("de la Fuente") no se puede adivinar; el segundo
    apellido solo es obligatorio para documentos espanoles y el mostrador
    puede revisarlo en el XML antes de enviar.
    """
    partes = (apellidos or "").strip().split(" ", 1)
    return partes[0], (partes[1] if len(partes) > 1 else "")


def _direccion(obj) -> dict:
    return {
        "direccion": getattr(obj, "address", "") or "",
        "codigo_postal": getattr(obj, "postal_code", "") or "",
        "municipio": getattr(obj, "city", "") or "",
        "provincia": getattr(obj, "province", "") or "",
        "pais": getattr(obj, "country", "") or "",
    }


def _persona(cliente, rol: str) -> dict:
    apellido1, apellido2 = _apellidos(cliente.last_name)
    return {
        "rol": rol,
        "nombre": cliente.first_name,
        "apellido1": apellido1,
        "apellido2": apellido2,
        "tipo_documento": TIPO_DOCUMENTO.get(cliente.document_type, ""),
        "tipo_documento_interno": cliente.document_type,
        "numero_documento": cliente.document_number,
        "soporte_documento": getattr(cliente, "document_support", "") or "",
        "fecha_nacimiento": _fecha(cliente.birth_date),
        "nacionalidad": cliente.nationality or "",
        "sexo": SEXO.get(getattr(cliente, "sex", ""), ""),
        "telefono": cliente.phone or "",
        "correo": cliente.email or "",
        "permiso_conducir": cliente.licence_number or "",
        **_direccion(cliente),
    }


def _conductor(conductor) -> dict:
    apellido1, apellido2 = _apellidos(conductor.last_name)
    return {
        "rol": "CO",
        "nombre": conductor.first_name,
        "apellido1": apellido1,
        "apellido2": apellido2,
        "tipo_documento": "",
        "tipo_documento_interno": "",
        "numero_documento": conductor.document_number or "",
        "soporte_documento": "",
        "fecha_nacimiento": _fecha(conductor.birth_date),
        "nacionalidad": conductor.licence_country or "",
        "sexo": "",
        "telefono": "",
        "correo": "",
        "permiso_conducir": conductor.licence_number or "",
        "direccion": "",
        "codigo_postal": "",
        "municipio": "",
        "provincia": "",
        "pais": "",
    }


def build_payload(reservation, *, landlord_code: str, drivers=(), payment_method: str = "") -> dict:
    """Todo lo que se comunica de un contrato, en un dict serializable.

    `drivers` son los conductores adicionales; el titular del contrato va
    siempre como arrendatario y conductor principal.
    """
    vehiculo = reservation.vehicle
    entrega = reservation.actual_pickup_at or reservation.pickup_at
    personas = []
    if reservation.customer is not None:
        personas.append(_persona(reservation.customer, "TI"))
    personas.extend(_conductor(conductor) for conductor in drivers)
    return {
        "tipo_comunicacion": TIPO_COMUNICACION,
        "codigo_arrendador": landlord_code,
        "contrato": {
            "referencia": reservation.number,
            "fecha_contrato": _fecha(timezone.localdate(entrega)) if entrega else "",
            "fecha_hora_inicio": _momento(entrega),
            "fecha_hora_fin": _momento(reservation.return_at),
            "lugar_entrega": _direccion(reservation.pickup_office),
            "lugar_devolucion": _direccion(reservation.return_office),
            "tipo_pago": TIPO_PAGO.get(payment_method, ""),
        },
        "vehiculo": {
            "matricula": vehiculo.plate if vehiculo else "",
            "marca": vehiculo.brand if vehiculo else "",
            "modelo": vehiculo.model if vehiculo else "",
            "color": (vehiculo.color if vehiculo else "") or "",
            "kilometros": getattr(getattr(reservation, "check_in", None), "mileage", None),
        },
        "personas": personas,
    }


def missing_fields(payload: dict) -> list[str]:
    """Lo que falta para poder comunicar el contrato, en lenguaje de mostrador."""
    faltan = []
    if not payload.get("codigo_arrendador"):
        faltan.append("Código de arrendador de SES (configuración SES_LANDLORD_CODE)")
    contrato = payload["contrato"]
    if not contrato["fecha_hora_inicio"]:
        faltan.append("Fecha y hora de entrega")
    if not contrato["lugar_entrega"]["direccion"]:
        faltan.append("Dirección de la oficina de entrega")
    if not contrato["tipo_pago"]:
        faltan.append("Medio de pago (registra el cobro o el anticipo)")
    vehiculo = payload["vehiculo"]
    for clave, etiqueta in (
        ("matricula", "Matrícula del vehículo asignado"),
        ("marca", "Marca del vehículo"),
        ("modelo", "Modelo del vehículo"),
        ("color", "Color del vehículo"),
    ):
        if not vehiculo[clave]:
            faltan.append(etiqueta)

    titulares = [p for p in payload["personas"] if p["rol"] == "TI"]
    if not titulares:
        faltan.append("Cliente de la reserva")
    for persona in payload["personas"]:
        quien = f"{persona['nombre']} {persona['apellido1']}".strip() or "Conductor"
        obligatorios = [
            ("numero_documento", "número de documento"),
            ("fecha_nacimiento", "fecha de nacimiento"),
            ("permiso_conducir", "permiso de conducir"),
        ]
        if persona["rol"] == "TI":
            obligatorios += [
                ("tipo_documento", "tipo de documento"),
                ("nacionalidad", "nacionalidad"),
                ("sexo", "sexo"),
                ("direccion", "dirección"),
                ("municipio", "localidad"),
                ("pais", "país"),
            ]
            if persona["tipo_documento_interno"] in CON_SOPORTE:
                obligatorios.append(("soporte_documento", "número de soporte del documento"))
            if persona["nacionalidad"] == "ES" and not persona["apellido2"]:
                faltan.append(f"{quien}: segundo apellido")
            if not persona["telefono"] and not persona["correo"]:
                faltan.append(f"{quien}: teléfono o correo")
        for clave, etiqueta in obligatorios:
            if not persona.get(clave):
                faltan.append(f"{quien}: {etiqueta}")
    return faltan


def _sub(padre, etiqueta: str, valor) -> None:
    if valor in (None, ""):
        return
    ET.SubElement(padre, etiqueta).text = str(valor)


def _direccion_xml(padre, etiqueta: str, datos: dict) -> None:
    nodo = ET.SubElement(padre, etiqueta)
    _sub(nodo, "direccion", datos["direccion"])
    _sub(nodo, "codigoPostal", datos["codigo_postal"])
    _sub(nodo, "nombreMunicipio", datos["municipio"])
    _sub(nodo, "provincia", datos["provincia"])
    _sub(nodo, "pais", datos["pais"])


def to_xml(payload: dict) -> str:
    """El parte como XML. Una comunicacion de alta con un contrato."""
    raiz = ET.Element("peticion")
    cabecera = ET.SubElement(raiz, "cabecera")
    _sub(cabecera, "codigoArrendador", payload["codigo_arrendador"])
    _sub(cabecera, "tipoComunicacion", payload["tipo_comunicacion"])
    _sub(cabecera, "tipoOperacion", "A")

    solicitud = ET.SubElement(raiz, "solicitud")
    comunicacion = ET.SubElement(solicitud, "comunicacion")

    contrato = payload["contrato"]
    nodo = ET.SubElement(comunicacion, "contrato")
    _sub(nodo, "referencia", contrato["referencia"])
    _sub(nodo, "fechaContrato", contrato["fecha_contrato"])
    _sub(nodo, "fechaHoraInicio", contrato["fecha_hora_inicio"])
    _sub(nodo, "fechaHoraFin", contrato["fecha_hora_fin"])
    _direccion_xml(nodo, "lugarEntrega", contrato["lugar_entrega"])
    _direccion_xml(nodo, "lugarDevolucion", contrato["lugar_devolucion"])
    pago = ET.SubElement(nodo, "pago")
    _sub(pago, "tipoPago", contrato["tipo_pago"])

    vehiculo = payload["vehiculo"]
    nodo = ET.SubElement(comunicacion, "vehiculo")
    _sub(nodo, "matricula", vehiculo["matricula"])
    _sub(nodo, "marca", vehiculo["marca"])
    _sub(nodo, "modelo", vehiculo["modelo"])
    _sub(nodo, "color", vehiculo["color"])

    for persona in payload["personas"]:
        nodo = ET.SubElement(comunicacion, "persona")
        _sub(nodo, "rol", persona["rol"])
        _sub(nodo, "nombre", persona["nombre"])
        _sub(nodo, "apellido1", persona["apellido1"])
        _sub(nodo, "apellido2", persona["apellido2"])
        _sub(nodo, "tipoDocumento", persona["tipo_documento"])
        _sub(nodo, "numeroDocumento", persona["numero_documento"])
        _sub(nodo, "soporteDocumento", persona["soporte_documento"])
        _sub(nodo, "fechaNacimiento", persona["fecha_nacimiento"])
        _sub(nodo, "nacionalidad", persona["nacionalidad"])
        _sub(nodo, "sexo", persona["sexo"])
        _sub(nodo, "telefono", persona["telefono"])
        _sub(nodo, "correo", persona["correo"])
        _sub(nodo, "permisoConducir", persona["permiso_conducir"])
        if persona["direccion"]:
            _direccion_xml(nodo, "direccion", persona)

    ET.indent(raiz)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(raiz, encoding="unicode")
