import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class IdentityManager(BaseUserManager):
    use_in_migrations = True

    def get_or_create_from_claims(self, claims):
        """Get or create an Identity from TokenClaims."""
        return self.get_or_create(
            issuer=claims.issuer,
            sub=claims.sub,
        )

    def _create_user(self, email, password, **extra_fields):
        """Create a local (urn:local) identity with email/password."""
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        extra_fields.pop("sub", None)
        identity = self.model(
            issuer="urn:local",
            sub=str(uuid.uuid4()),
            email=email,
            **extra_fields,
        )
        identity.set_password(password)
        identity.save(using=self._db)
        return identity

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class Identity(AbstractBaseUser, PermissionsMixin):
    """OIDC-based identity model: (issuer, sub) tuple.

    External identities (Firebase, SAML): no user data stored locally.
    Local identities (iss="urn:local"): email + password for admin/standalone.
    """
    issuer = models.CharField(max_length=255)
    sub = models.CharField(max_length=255, unique=True)

    email = models.EmailField(blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    objects = IdentityManager()

    USERNAME_FIELD = "sub"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "identity"
        verbose_name_plural = "identities"
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "sub"],
                name="uq_identity_issuer_sub",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(issuer="urn:local") & ~models.Q(email=""),
                name="uq_identity_local_email",
            ),
        ]

    @property
    def is_local(self) -> bool:
        return self.issuer == "urn:local"

    def __str__(self):
        if self.email:
            return self.email
        return f"{self.issuer}|{self.sub}"
