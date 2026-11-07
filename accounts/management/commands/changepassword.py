"""`changepassword`, resolving the username the way login does (#700).

Django's own command finds the user with `.get(sub=<username>)`. Since #700
`sub` is unique only within an issuer, so that lookup raises
`MultipleObjectsReturned` as soon as a local identity and an external one
share a subject, which is the case #700 exists for. Only `urn:local`
identities have usable passwords, so the name is resolved through
`get_by_natural_key`, which scopes a bare username to them, exactly as
`ModelBackend` does at login.

This replaces django.contrib.auth's command because `accounts` is listed
before `django.contrib.auth` in INSTALLED_APPS: Django lets the earlier app
win. `handle` is Django 5.2's, with only the lookup changed.
"""
import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.management.commands import changepassword
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError

UserModel = get_user_model()


class Command(changepassword.Command):
    def handle(self, *args, **options):
        if options["username"]:
            username = options["username"]
        else:
            username = getpass.getuser()

        try:
            u = UserModel._default_manager.db_manager(
                options["database"]
            ).get_by_natural_key(username)
        except UserModel.DoesNotExist:
            raise CommandError("user '%s' does not exist" % username)

        self.stdout.write("Changing password for user '%s'" % u)

        MAX_TRIES = 3
        count = 0
        p1, p2 = 1, 2  # To make them initially mismatch.
        password_validated = False
        while (p1 != p2 or not password_validated) and count < MAX_TRIES:
            p1 = self._get_pass()
            p2 = self._get_pass("Password (again): ")
            if p1 != p2:
                self.stdout.write("Passwords do not match. Please try again.")
                count += 1
                # Don't validate passwords that don't match.
                continue
            try:
                validate_password(p2, u)
            except ValidationError as err:
                self.stderr.write("\n".join(err.messages))
                count += 1
            else:
                password_validated = True

        if count == MAX_TRIES:
            raise CommandError(
                "Aborting password change for user '%s' after %s attempts" % (u, count)
            )

        u.set_password(p1)
        u.save()

        return "Password changed successfully for user '%s'" % u
