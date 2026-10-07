import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from .catalog import CATEGORIES
from .stages import LANG_CHOICES, STAGE_CHOICES


def new_track_token():
    # 96 random bits: the tracking link is the only thing a customer needs, so it must not be guessable.
    return secrets.token_urlsafe(12)


class Job(models.Model):
    PICKUP, DELIVERY = "Pickup", "Delivery"
    MODE_CHOICES = [(PICKUP, PICKUP), (DELIVERY, DELIVERY)]

    number = models.PositiveIntegerField(unique=True)
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=10)
    site = models.CharField(max_length=120, blank=True)
    contractor = models.CharField(max_length=80, blank=True)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=PICKUP)
    due = models.DateField()
    notes = models.CharField(max_length=500, blank=True)
    # Owner-only. Never serialised for workers or shown on the tracking page.
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    track_token = models.CharField(max_length=32, unique=True, default=new_track_token)
    # Moves whenever a step is ticked or undone.
    stage_changed_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "jobs"
        ordering = ["number"]

    @property
    def code(self):
        return f"WS-{self.number}"

    def __str__(self):
        return f"{self.code} {self.customer_name}"


class JobItem(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="items")
    category = models.CharField(max_length=20, choices=[(c["key"], c["label"]) for c in CATEGORIES])
    brand = models.CharField(max_length=60, blank=True)
    thickness = models.CharField(max_length=60, blank=True)
    size = models.CharField(max_length=60, blank=True)
    pack = models.CharField(max_length=60, blank=True)
    qty = models.PositiveIntegerField()
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "job_items"
        ordering = ["position", "id"]


class StageTick(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="ticks")
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    done_by_name = models.CharField(max_length=80)
    done_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "stage_ticks"
        constraints = [models.UniqueConstraint(fields=["job", "stage"], name="one_tick_per_stage")]


class AuditEntry(models.Model):
    """Append-only. Nothing in the app updates or deletes these rows."""

    CREATED, EDITED, TICKED, REVERSED = "created", "edited", "ticked", "reversed"
    ACTION_CHOICES = [(a, a) for a in (CREATED, EDITED, TICKED, REVERSED)]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="audit")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    # Name is copied so the trail still reads correctly after a staff account is removed.
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_name = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "audit_entries"
        ordering = ["created_at", "id"]


class RosterDay(models.Model):
    """A day whose duties have been set. Its existence, even with no Duty rows, means
    'decided for this day', so a new day copies the last decided one exactly once."""

    day = models.DateField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "roster_days"


class Duty(models.Model):
    """One worker is on one step for one day."""

    day = models.DateField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="duties")
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)

    class Meta:
        db_table = "duties"
        constraints = [models.UniqueConstraint(fields=["day", "user", "stage"], name="one_duty_per_worker_step_day")]


class WorkSent(models.Model):
    """The owner sent this worker their work message for this day."""

    day = models.DateField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "work_sent"
        constraints = [models.UniqueConstraint(fields=["day", "user"], name="one_work_message_per_worker_day")]


class MessageWording(models.Model):
    """The shop's own wording for one message in one language. No row means the standard
    wording from catalog.TEMPLATE_DEFAULTS."""

    key = models.CharField(max_length=20)
    lang = models.CharField(max_length=2, choices=LANG_CHOICES)
    body = models.CharField(max_length=700)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "message_wordings"
        constraints = [models.UniqueConstraint(fields=["key", "lang"], name="one_wording_per_message_language")]
