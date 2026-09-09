"""Tonos de los badges de estado.

El color es presentacion, no dominio: cada app registra el tono de sus estados
al arrancar (`AppConfig.ready`) y aqui solo vive la paleta y el registro.
"""

TONES = {
    "neutral": "bg-slate-100 text-slate-700 ring-slate-500/20",
    "info": "bg-sky-50 text-sky-700 ring-sky-600/20",
    "accent": "bg-brand-50 text-brand-700 ring-brand-600/20",
    "success": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "warning": "bg-amber-50 text-amber-800 ring-amber-600/30",
    "danger": "bg-rose-50 text-rose-700 ring-rose-600/20",
}

DEFAULT_TONE = "neutral"

_STATUS_TONES: dict[str, str] = {
    # Estados transversales de los maestros (ActivableModel).
    "active": "success",
    "inactive": "neutral",
}


def register(status: str, tone: str) -> None:
    """Asocia un estado a un tono. Lanza KeyError si el tono no existe."""
    if tone not in TONES:
        raise KeyError(f"Tono desconocido: {tone!r}. Disponibles: {sorted(TONES)}")
    _STATUS_TONES[status] = tone


def tone_for(status: str) -> str:
    return _STATUS_TONES.get(status, DEFAULT_TONE)


def classes_for(status: str) -> str:
    return TONES[tone_for(status)]
