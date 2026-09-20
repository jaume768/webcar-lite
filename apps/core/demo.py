"""Datos ficticios para /ui-kit/. Se borra junto con la vista de demostracion.

No hay modelos de negocio todavia, asi que el catalogo se genera en memoria de
forma determinista (misma semilla, mismas filas) y se cachea en el proceso.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from random import Random

from django import forms

from . import badges
from . import forms as core_forms

TOTAL_FILAS = 10_000
SEMILLA = 20240101

CATEGORIAS = [
    ("A", "Economico"),
    ("B", "Compacto"),
    ("C", "Familiar"),
    ("D", "Monovolumen"),
    ("E", "Furgoneta"),
    ("F", "Premium"),
]

MODELOS = {
    "A": ["Fiat Panda", "Kia Picanto", "Toyota Aygo"],
    "B": ["Seat Ibiza", "Renault Clio", "Opel Corsa"],
    "C": ["Seat Leon", "Peugeot 308", "Skoda Octavia"],
    "D": ["Citroen Berlingo", "Renault Scenic", "Ford Tourneo"],
    "E": ["Renault Trafic", "Fiat Ducato", "Mercedes Vito"],
    "F": ["BMW Serie 3", "Audi A4", "Volvo XC40"],
}

OFICINAS = ["Centro", "Norte", "Sur", "Aeropuerto"]

ESTADOS = [
    ("disponible", "Disponible", "success"),
    ("alquilado", "Alquilado", "accent"),
    ("taller", "En taller", "warning"),
    ("reservado", "Reservado", "info"),
    ("baja", "De baja", "danger"),
]

# Los tonos de estos estados los registra el propio modulo que los usa.
for codigo, _etiqueta, tono in ESTADOS:
    badges.register(codigo, tono)


@dataclass(frozen=True, slots=True)
class VehiculoDemo:
    id: int
    plate: str
    model: str
    category_code: str
    category: str
    office: str
    status: str
    status_label: str
    daily_rate: Decimal
    registered_on: date

    @property
    def search_text(self) -> str:
        return f"{self.plate} {self.model} {self.office}".lower()


@lru_cache(maxsize=1)
def catalogo() -> tuple[VehiculoDemo, ...]:
    """10.000 vehiculos ficticios, siempre los mismos."""
    azar = Random(SEMILLA)
    letras = "BCDFGHJKLMNPRSTVWXYZ"
    inicio = date(2019, 1, 1)
    filas = []

    for numero in range(1, TOTAL_FILAS + 1):
        codigo, categoria = azar.choice(CATEGORIAS)
        estado, etiqueta, _tono = azar.choice(ESTADOS)
        matricula = f"{azar.randint(1000, 9999)} {''.join(azar.choice(letras) for _ in range(3))}"
        filas.append(
            VehiculoDemo(
                id=numero,
                plate=matricula,
                model=azar.choice(MODELOS[codigo]),
                category_code=codigo,
                category=categoria,
                office=azar.choice(OFICINAS),
                status=estado,
                status_label=etiqueta,
                daily_rate=Decimal(azar.randrange(1800, 14500, 50)) / 100,
                registered_on=inicio + timedelta(days=azar.randint(0, 2200)),
            )
        )

    return tuple(filas)


def filtrar(*, q: str = "", categoria: str = "", estado: str = "") -> list[VehiculoDemo]:
    """Busqueda por texto libre mas filtros exactos."""
    filas = catalogo()
    termino = q.strip().lower()

    if termino:
        filas = [fila for fila in filas if termino in fila.search_text]
    if categoria:
        filas = [fila for fila in filas if fila.category_code == categoria]
    if estado:
        filas = [fila for fila in filas if fila.status == estado]

    return list(filas)


class FormularioDemo(forms.Form):
    """Formulario de muestra para /ui-kit/: ejercita campos, fechas y errores."""

    reference = forms.CharField(
        label="Referencia",
        max_length=12,
        help_text="Codigo interno de la reserva de ejemplo.",
    )
    pickup_at = core_forms.DateTimeField(label="Recogida")
    return_at = core_forms.DateTimeField(label="Devolucion")
    birth_date = core_forms.DateField(label="Fecha de nacimiento", required=False)
    with_insurance = forms.BooleanField(label="Con seguro a todo riesgo", required=False)

    def clean(self):
        datos = super().clean()
        recogida, devolucion = datos.get("pickup_at"), datos.get("return_at")
        if recogida and devolucion and devolucion <= recogida:
            self.add_error("return_at", "La devolucion tiene que ser posterior a la recogida.")
        return datos
