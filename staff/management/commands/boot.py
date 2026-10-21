from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Everything that runs before the web server starts, in one process: make sure the
    database exists, bring its tables up to date, make the first owner account.

    On Render's free plan the service sleeps after 15 idle minutes and every wake runs the
    start command again. Three separate `manage.py` calls start Python and load Django three
    times on a small, slow CPU; this does it once. The steps and their order are unchanged."""

    help = "ensure_database, migrate and bootstrap_owner, in one go."

    def handle(self, *args, **options):
        call_command("ensure_database")
        call_command("migrate", interactive=False, verbosity=1)
        call_command("bootstrap_owner")
