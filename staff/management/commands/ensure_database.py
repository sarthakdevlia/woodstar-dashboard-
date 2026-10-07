import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from psycopg import sql


class Command(BaseCommand):
    """Runs on every boot, before migrate. DATABASE_URL names WoodStar's own database on a
    Postgres instance that other apps share; a managed instance gives no place to type
    CREATE DATABASE, and its port is often closed to the outside. So the app makes its
    database itself, from inside, the first time it starts. After that this does nothing."""

    help = "Create the database named in DATABASE_URL on its Postgres instance, if it does not exist."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        if "postgresql" not in db["ENGINE"]:
            self.stdout.write("Not Postgres; nothing to create.")
            return
        name, user = db["NAME"], db["USER"]
        if not name:
            raise CommandError("DATABASE_URL has no database name after the last '/'.")
        # A database that is sure to exist to connect through: Postgres's own, else the one
        # Render made for this user (its name is the user's without the "_user" ending).
        doors = ["postgres"] + ([user.removesuffix("_user")] if user.endswith("_user") else [])
        last_error = None
        for door in doors:
            try:
                with psycopg.connect(
                    dbname=door, user=user, password=db["PASSWORD"], host=db["HOST"], port=db["PORT"] or None,
                    autocommit=True, connect_timeout=20, **db.get("OPTIONS", {}),
                ) as conn:
                    if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                        self.stdout.write(f'Database "{name}" already exists.')
                    else:
                        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                        self.stdout.write(self.style.SUCCESS(f'Created database "{name}".'))
                    return
            except psycopg.Error as exc:
                last_error = exc
        raise CommandError(f'Could not make sure the database "{name}" exists: {str(last_error).strip().splitlines()[0]}')
