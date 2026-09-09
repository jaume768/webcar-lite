"""Usuarios, roles y permisos. El acceso es privado: no hay alta publica."""

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    Permission,
    PermissionsMixin,
)
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Role(TimeStampedModel):
    """Conjunto de permisos con nombre.

    Los permisos efectivos de un usuario son los de su rol mas los que tenga
    asignados a titulo individual. El conjunto por defecto de cada rol se
    declara en `roles.py` y se aplica con `manage.py sync_roles`.
    """

    code = models.SlugField(_("codigo"), max_length=40, unique=True)
    name = models.CharField(_("nombre"), max_length=80)
    description = models.TextField(_("descripcion"), blank=True)
    permissions = models.ManyToManyField(
        Permission,
        verbose_name=_("permisos"),
        blank=True,
        related_name="roles",
    )
    is_system = models.BooleanField(
        _("del sistema"),
        default=False,
        help_text=_("Definido en codigo. sync_roles lo mantiene al dia."),
    )

    class Meta:
        verbose_name = _("rol")
        verbose_name_plural = _("roles")
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    """Alta de usuarios por correo. No hay nombre de usuario."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError(_("El correo electronico es obligatorio."))
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Un superusuario tiene que ser staff."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Un superusuario tiene que tener is_superuser=True."))
        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, username):
        # El correo no distingue mayusculas: en mostrador se teclea como sale.
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    email = models.EmailField(_("correo electronico"), unique=True)
    first_name = models.CharField(_("nombre"), max_length=80, blank=True)
    last_name = models.CharField(_("apellidos"), max_length=120, blank=True)
    phone = models.CharField(_("telefono"), max_length=20, blank=True)

    role = models.ForeignKey(
        Role,
        verbose_name=_("rol"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    offices = models.ManyToManyField(
        "offices.Office",
        verbose_name=_("oficinas"),
        blank=True,
        related_name="users",
        help_text=_("Oficinas sobre las que puede operar. Sin oficinas no ve datos."),
    )

    is_active = models.BooleanField(
        _("activo"),
        default=True,
        help_text=_("Un usuario desactivado no puede entrar. Nunca se borra."),
    )
    is_staff = models.BooleanField(
        _("soporte tecnico"),
        default=False,
        help_text=_("Da acceso al admin interno de Django. No es el panel de gestion."),
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ["last_name", "first_name", "email"]
        permissions = [
            ("manage_users", _("Puede gestionar usuarios, roles y oficinas asignadas")),
        ]

    def __str__(self):
        return self.get_full_name() or self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name or self.email

    @property
    def initials(self) -> str:
        iniciales = f"{self.first_name[:1]}{self.last_name[:1]}".strip()
        return (iniciales or self.email[:2]).upper()
