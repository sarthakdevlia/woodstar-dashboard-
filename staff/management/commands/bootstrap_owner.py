import os

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from jobs.stages import OWNER
from staff.models import User


class Command(BaseCommand):
    """Runs on every boot. A small Render instance has no shell, so the first owner account
    comes from OWNER_USERNAME / OWNER_PASSWORD / OWNER_NAME. It never touches an account
    that already exists, so the password can be changed in the app afterwards."""

    help = "Create the first owner account from environment variables, if it does not exist."

    def handle(self, *args, **options):
        username = os.environ.get("OWNER_USERNAME", "").strip()
        password = os.environ.get("OWNER_PASSWORD", "")
        if not username:
            self.stdout.write("OWNER_USERNAME not set; skipping owner bootstrap.")
            return
        if User.objects.filter(username__iexact=username).exists():
            self.stdout.write(f"Owner account '{username}' already exists.")
            return
        if not password:
            raise CommandError("OWNER_PASSWORD must be set to create the owner account.")
        user = User(username=username, name=os.environ.get("OWNER_NAME", username).strip(), role=OWNER, lang="en")
        validate_password(password, user)
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Created owner account '{username}'."))
