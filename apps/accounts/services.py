"""Operaciones sobre usuarios. Las vistas orquestan, aqui esta la logica."""

import secrets

import structlog
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import User

logger = structlog.get_logger(__name__)


class UserServiceError(Exception):
    """Regla de negocio incumplida al operar sobre un usuario."""


@transaction.atomic
def create_user(*, form_data: dict, actor: User) -> User:
    """Da de alta un usuario sin contrasena utilizable.

    No se inventa una contrasena que haya que comunicar por telefono: se crea
    la cuenta bloqueada y el usuario la estrena con el enlace de reseteo.
    """
    offices = form_data.pop("offices", [])
    user = User(**form_data)
    # Contrasena imposible de adivinar y que nadie conoce, ni siquiera nosotros.
    user.set_password(secrets.token_urlsafe(32))
    user.full_clean(exclude=["password"])
    user.save()
    user.offices.set(offices)

    logger.info(
        "usuario_creado",
        user_id=user.pk,
        email=user.email,
        role=user.role.code if user.role else None,
        offices=[o.code for o in offices],
        actor_id=actor.pk,
    )
    return user


@transaction.atomic
def update_user(*, user: User, form_data: dict, actor: User) -> User:
    offices = form_data.pop("offices", None)
    for campo, valor in form_data.items():
        setattr(user, campo, valor)
    user.full_clean(exclude=["password"])
    user.save()
    if offices is not None:
        user.offices.set(offices)

    logger.info("usuario_modificado", user_id=user.pk, actor_id=actor.pk)
    return user


@transaction.atomic
def deactivate_user(*, user: User, actor: User) -> User:
    """Desactiva. Nunca borra: un borrado deja la auditoria coja."""
    if user.pk == actor.pk:
        raise UserServiceError(_("No puedes desactivar tu propia cuenta."))
    if user.is_superuser and not actor.is_superuser:
        raise UserServiceError(_("Solo un superusuario puede desactivar a otro."))

    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    logger.warning("usuario_desactivado", user_id=user.pk, actor_id=actor.pk)
    return user


@transaction.atomic
def activate_user(*, user: User, actor: User) -> User:
    user.is_active = True
    user.save(update_fields=["is_active", "updated_at"])
    logger.info("usuario_reactivado", user_id=user.pk, actor_id=actor.pk)
    return user


def password_reset_token(user: User) -> str:
    """Token del enlace para estrenar o recuperar la contrasena."""
    return default_token_generator.make_token(user)
