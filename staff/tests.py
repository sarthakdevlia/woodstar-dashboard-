from io import StringIO
from unittest import mock

import psycopg
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

POSTGRES = {"default": {
    "ENGINE": "django.db.backends.postgresql", "NAME": "woodstar", "USER": "shop_db_user", "PASSWORD": "x",
    "HOST": "db.internal", "PORT": "", "OPTIONS": {},
}}


def fake_connection(exists):
    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (1,) if exists else None
    return conn


@override_settings(DATABASES=POSTGRES)
class EnsureDatabaseTests(SimpleTestCase):
    def run_command(self):
        out = StringIO()
        call_command("ensure_database", stdout=out)
        return out.getvalue()

    def test_creates_the_database_when_it_is_missing(self):
        conn = fake_connection(exists=False)
        with mock.patch("psycopg.connect", return_value=conn) as connect:
            self.assertIn('Created database "woodstar"', self.run_command())
        self.assertEqual(connect.call_args.kwargs["dbname"], "postgres")     # connects through a database that exists
        self.assertTrue(connect.call_args.kwargs["autocommit"])              # CREATE DATABASE cannot run in a transaction
        created = conn.execute.call_args_list[-1].args[0]
        self.assertEqual(created.as_string(None), 'CREATE DATABASE "woodstar"')

    def test_leaves_an_existing_database_alone(self):
        conn = fake_connection(exists=True)
        with mock.patch("psycopg.connect", return_value=conn):
            self.assertIn("already exists", self.run_command())
        self.assertEqual(conn.execute.call_count, 1)

    def test_falls_back_to_the_users_own_database(self):
        conn = fake_connection(exists=True)
        with mock.patch("psycopg.connect", side_effect=[psycopg.OperationalError("no access to postgres"), conn]) as connect:
            self.run_command()
        self.assertEqual([c.kwargs["dbname"] for c in connect.call_args_list], ["postgres", "shop_db"])

    def test_says_why_when_it_cannot_connect(self):
        with mock.patch("psycopg.connect", side_effect=psycopg.OperationalError("connection refused")):
            with self.assertRaisesMessage(CommandError, "connection refused"):
                self.run_command()


class EnsureDatabaseLocalTests(SimpleTestCase):
    def test_does_nothing_on_sqlite(self):
        out = StringIO()
        with mock.patch("psycopg.connect") as connect:
            call_command("ensure_database", stdout=out)
        connect.assert_not_called()
        self.assertIn("Not Postgres", out.getvalue())
