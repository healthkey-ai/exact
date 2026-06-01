from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Identity


@admin.register(Identity)
class IdentityAdmin(BaseUserAdmin):
    list_display = ("email", "issuer", "sub", "is_staff", "created_at")
    list_filter = ("issuer", "is_staff", "is_active")
    search_fields = ("email", "sub")
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("sub", "password")}),
        ("Identity", {"fields": ("issuer", "email", "name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )
    readonly_fields = ("created_at", "issuer", "sub")
