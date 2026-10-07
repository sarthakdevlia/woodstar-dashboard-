from django.contrib.auth.models import AbstractUser
from django.db import models

from jobs.stages import LANG_CHOICES, OWNER, ROLE_CHOICES, ROLE_TITLES


class User(AbstractUser):
    """A person who signs in to the dashboard: the owner, or a worker. A worker's steps are
    not fixed on the account; the owner gives each one their duty day by day (jobs.Duty)."""

    name = models.CharField(max_length=80)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    # Where their daily work message goes, and in which language.
    phone = models.CharField(max_length=10, blank=True)
    lang = models.CharField(max_length=2, choices=LANG_CHOICES, default="hi")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        ordering = ["name"]

    @property
    def is_owner(self):
        return self.role == OWNER

    @property
    def title(self):
        return ROLE_TITLES.get(self.role, self.role)

    def __str__(self):
        return f"{self.name} ({self.title})"
