"""Every change goes through here: permission check, row lock, write, audit.
Views never touch the models directly for writes."""

import re

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .catalog import CATEGORY_BY_KEY, TEMPLATE_DEFAULTS
from .models import AuditEntry, Duty, Job, JobItem, MessageWording, RosterDay, StageTick, WorkSent
from .permissions import check_create, check_tick
from .stages import STAGE_KEYS, STAGE_LABELS, WORKER

WORDING_MIN, WORDING_MAX = 30, 700
BLANK = re.compile(r"\{(\w+)\}")


class Denied(Exception):
    """A rule stopped the change. The message is safe to show the user."""


def today():
    return timezone.localdate()


def _audit(job, user, action, stage="", detail=""):
    AuditEntry.objects.create(job=job, actor=user, actor_name=user.name, action=action, stage=stage, detail=detail)


def _require_owner(user, action):
    if not user.is_owner:
        raise Denied(f"Only the owner can {action}.")


# ---------------------------------------------------------------- duties

def _workers():
    from staff.models import User
    return User.objects.filter(role=WORKER, is_active=True)


def _ensure_roster(day):
    """A new day starts with the last decided day's duties, ready to change."""
    try:
        with transaction.atomic():
            _, created = RosterDay.objects.get_or_create(day=day)
            if not created:
                return
            last = RosterDay.objects.filter(day__lt=day).order_by("-day").first()
            if last:
                Duty.objects.bulk_create([
                    Duty(day=day, user_id=d.user_id, stage=d.stage)
                    for d in Duty.objects.filter(day=last.day, user__in=_workers())
                ])
    except IntegrityError:
        pass  # another request set the day up at the same moment


def roster_for(day):
    """{worker id: [steps, in floor order]} for every active worker with a duty that day."""
    _ensure_roster(day)
    roster = {}
    for duty in Duty.objects.filter(day=day, user__in=_workers()):
        roster.setdefault(duty.user_id, []).append(duty.stage)
    for steps in roster.values():
        steps.sort(key=STAGE_KEYS.index)
    return roster


def duties_of(user, day=None):
    if user.is_owner:
        return list(STAGE_KEYS)
    return roster_for(day or today()).get(user.id, [])


def _on_duty_names(stage, day):
    return list(_workers().filter(duties__day=day, duties__stage=stage).values_list("name", flat=True))


@transaction.atomic
def set_duty(actor, user_id, stage, on):
    _require_owner(actor, "change duties")
    day = today()
    _ensure_roster(day)
    worker = _workers().filter(pk=user_id).first()
    if not worker:
        raise Denied("That worker is not on the team.")
    if on:
        Duty.objects.get_or_create(day=day, user=worker, stage=stage)
    else:
        Duty.objects.filter(day=day, user=worker, stage=stage).delete()
    # Their message has changed, so it needs sending again.
    WorkSent.objects.filter(day=day, user=worker).delete()


@transaction.atomic
def mark_work_sent(actor, user_ids):
    _require_owner(actor, "send the day's work")
    day, now = today(), timezone.now()
    for worker in _workers().filter(pk__in=user_ids):
        WorkSent.objects.update_or_create(day=day, user=worker, defaults={"sent_by": actor, "sent_at": now})


def sent_for(day):
    return {s.user_id: s.sent_at for s in WorkSent.objects.filter(day=day)}


# ---------------------------------------------------------------- job cards

def _locked(job_number):
    return Job.objects.select_for_update().get(number=job_number)


def _done(job):
    return set(job.ticks.values_list("stage", flat=True))


def next_job_number():
    last = Job.objects.order_by("-number").values_list("number", flat=True).first()
    return max(settings.FIRST_JOB_NUMBER, (last or 0) + 1)


def _clean_item(raw, position):
    """Keep only the details that category asks for."""
    fields = CATEGORY_BY_KEY[raw["category"]]["fields"]
    return JobItem(
        category=raw["category"], qty=raw["qty"], position=position,
        **{f: (raw.get(f) or "").strip() for f in ("brand", "thickness", "size", "pack") if f in fields},
    )


def create_job(user, fields, items):
    reason = check_create(user.is_owner, duties_of(user))
    if reason:
        raise Denied(reason)
    if not user.is_owner:  # money is the owner's to enter
        fields = {k: v for k, v in fields.items() if k not in ("amount", "advance")}
    for _ in range(5):  # two counters saving at once would otherwise both take the same number
        try:
            with transaction.atomic():
                now = timezone.now()
                job = Job.objects.create(number=next_job_number(), created_by=user, created_at=now,
                                         stage_changed_at=now, **fields)
                for position, raw in enumerate(items):
                    item = _clean_item(raw, position)
                    item.job = job
                    item.save()
                _audit(job, user, AuditEntry.CREATED)
                # Writing the card is the first step.
                StageTick.objects.create(job=job, stage=STAGE_KEYS[0], done_by=user, done_by_name=user.name, done_at=now)
                return job
        except IntegrityError:
            continue
    raise Denied("Could not save the job card. Please try again.")


@transaction.atomic
def update_job(user, job_number, fields):
    _require_owner(user, "edit a job card")
    job = _locked(job_number)
    changed = [name for name, value in fields.items() if getattr(job, name) != value]
    if not changed:
        return job
    for name in changed:
        setattr(job, name, fields[name])
    job.save()
    _audit(job, user, AuditEntry.EDITED, detail=", ".join(c.replace("_", " ") for c in changed))
    return job


@transaction.atomic
def set_stage(user, job_number, stage, complete):
    job = _locked(job_number)
    done = _done(job)
    if (stage in done) == complete:
        return job
    day = today()
    reason = check_tick(user.is_owner, duties_of(user, day), stage, done, _on_duty_names(stage, day))
    if reason:
        raise Denied(reason)
    now = timezone.now()
    if complete:
        StageTick.objects.create(job=job, stage=stage, done_by=user, done_by_name=user.name, done_at=now)
        _audit(job, user, AuditEntry.TICKED, stage)
    else:
        job.ticks.filter(stage=stage).delete()
        _audit(job, user, AuditEntry.REVERSED, stage)
    job.stage_changed_at = now
    job.save(update_fields=["stage_changed_at", "updated_at"])
    return job


def audit_text(entry):
    stage = STAGE_LABELS.get(entry.stage, "")
    return {
        AuditEntry.CREATED: f"created the job card ({STAGE_LABELS[STAGE_KEYS[0]]})",
        AuditEntry.EDITED: f"edited {entry.detail}",
        AuditEntry.TICKED: f"marked {stage}",
        AuditEntry.REVERSED: f"undid {stage}",
    }[entry.action]


# ---------------------------------------------------------------- message wording

def wordings():
    """{message: {language: text}} — the shop's own wording where set, else the standard one."""
    out = {key: {"en": t["en"], "hi": t["hi"]} for key, t in TEMPLATE_DEFAULTS.items()}
    for row in MessageWording.objects.all():
        if row.key in out and row.lang in out[row.key]:
            out[row.key][row.lang] = row.body
    return out


def check_wording(key, text):
    """Why WhatsApp would refuse this wording as a template, or '' if it is fine."""
    template = TEMPLATE_DEFAULTS[key]
    used = BLANK.findall(text)
    unknown = [b for b in used if b not in template["blanks"]]
    if unknown:
        return f"{{{unknown[0]}}} is not one of the blanks."
    missing = [b for b in template["required"] if b not in used]
    if missing:
        return f"It must include {{{missing[0]}}}."
    stripped = text.strip()
    if stripped.startswith("{") or stripped.endswith("}"):
        return "It cannot begin or end with a blank — WhatsApp refuses that."
    if len(stripped) < WORDING_MIN:
        return f"Too short — at least {WORDING_MIN} characters."
    if len(stripped) > WORDING_MAX:
        return f"Too long — at most {WORDING_MAX} characters."
    return ""


@transaction.atomic
def save_wording(actor, key, lang, body):
    _require_owner(actor, "change the message wording")
    problem = check_wording(key, body)
    if problem:
        raise Denied(problem)
    MessageWording.objects.update_or_create(key=key, lang=lang, defaults={"body": body.strip(), "updated_by": actor})


@transaction.atomic
def reset_wording(actor, key, lang):
    _require_owner(actor, "change the message wording")
    MessageWording.objects.filter(key=key, lang=lang).delete()
