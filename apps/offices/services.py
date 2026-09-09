"""Altas y bajas de oficinas y de grupos de oficinas.

Las vistas orquestan; las reglas viven aqui. Ahora mismo son pocas (normalizar
el codigo y dejar rastro en el registro), pero es el sitio donde entraran las
que vengan: una oficina no se puede desactivar con reservas vivas, un grupo no
se desactiva con oficinas dentro, etc.
"""

import structlog
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import Office, OfficePool

logger = structlog.get_logger(__name__)


class OfficeServiceError(ServiceError):
    """Regla de negocio incumplida. La vista la convierte en aviso, no en 500."""


def _normalizar_codigo(valor: str) -> str:
    return (valor or "").strip().lower()


@transaction.atomic
def save_office(*, office: Office, actor=None) -> Office:
    """Crea o actualiza una oficina ya validada por su formulario."""
    creando = office.pk is None
    office.code = _normalizar_codigo(office.code)
    office.save()
    logger.info(
        "oficina_creada" if creando else "oficina_actualizada",
        office_id=office.pk,
        code=office.code,
        actor_id=getattr(actor, "pk", None),
    )
    return office


@transaction.atomic
def set_office_active(*, office: Office, active: bool, actor=None) -> Office:
    """Alta o baja logica de una oficina. Nunca se borra.

    Una oficina desactivada desaparece de los selectores y del scope de los
    usuarios, pero sigue siendo legible en el historico: las reservas y las
    facturas de hace dos anos apuntan a ella.
    """
    if not active and office.users.filter(is_active=True).exists():
        raise OfficeServiceError(
            _("No se puede desactivar %(oficina)s: tiene usuarios activos asignados.")
            % {"oficina": office.name}
        )

    if active:
        office.activate()
    else:
        office.deactivate()
    logger.info(
        "oficina_activada" if active else "oficina_desactivada",
        office_id=office.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return office


@transaction.atomic
def save_pool(*, pool: OfficePool, actor=None) -> OfficePool:
    """Crea o actualiza un grupo de oficinas ya validado por su formulario."""
    creando = pool.pk is None
    pool.code = _normalizar_codigo(pool.code)
    pool.save()
    logger.info(
        "pool_creado" if creando else "pool_actualizado",
        pool_id=pool.pk,
        code=pool.code,
        actor_id=getattr(actor, "pk", None),
    )
    return pool


@transaction.atomic
def set_pool_active(*, pool: OfficePool, active: bool, actor=None) -> OfficePool:
    """Alta o baja logica de un grupo. Un grupo con oficinas dentro no se baja.

    Desactivarlo dejaria la disponibilidad calculando sobre un grupo que la
    interfaz ya no ensena: mejor obligar a sacar antes las oficinas.
    """
    if not active and pool.offices.filter(is_active=True).exists():
        raise OfficeServiceError(
            _("No se puede desactivar %(pool)s: todavia tiene oficinas activas dentro.")
            % {"pool": pool.name}
        )

    if active:
        pool.activate()
    else:
        pool.deactivate()
    logger.info(
        "pool_activado" if active else "pool_desactivado",
        pool_id=pool.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return pool
