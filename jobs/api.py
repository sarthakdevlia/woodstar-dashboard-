from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view

from staff import services as staff_services
from staff.models import User

from . import services, whatsapp
from .catalog import TEMPLATE_DEFAULTS
from .models import Job, StageTick
from .responses import fail, ok
from .serializers import (
    DutySerializer, JobCreateSerializer, JobUpdateSerializer, MessageSerializer, SentSerializer,
    StaffCreateSerializer, StaffUpdateSerializer, WordingSerializer, job_to_dict, me_to_dict, staff_to_dict,
    worker_to_dict,
)
from .stages import STAGE_KEYS, WORKER

# Delivered cards stay on the board for two weeks, then only show with scope=all.
DELIVERED_VISIBLE_DAYS = 14
JOBS_MAX = 500
STAFF_MAX = 200


def _validated(serializer_class, data, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _job_detail(request, number):
    job = Job.objects.prefetch_related("ticks", "items").get(number=number)
    return job_to_dict(job, request.user, detail=True)


def _team():
    """Who is on the team today, their duties, and who has been sent their work."""
    day = services.today()
    roster = services.roster_for(day)
    sent = services.sent_for(day)
    workers = User.objects.filter(role=WORKER, is_active=True)[:STAFF_MAX]
    return {
        "workers": [worker_to_dict(w) for w in workers],
        "roster": {str(uid): steps for uid, steps in roster.items()},
        "sent": {str(uid): {"at": timezone.localtime(at).isoformat()} for uid, at in sent.items()},
    }


def _jobs(request):
    qs = Job.objects.prefetch_related("ticks", "items")
    if request.query_params.get("scope") != "all":
        cutoff = timezone.now() - timedelta(days=DELIVERED_VISIBLE_DAYS)
        old = StageTick.objects.filter(job=OuterRef("pk"), stage=STAGE_KEYS[-1], done_at__lt=cutoff)
        qs = qs.exclude(Exists(old))
    return [job_to_dict(j, request.user) for j in qs.order_by("-number")[:JOBS_MAX]]


@api_view(["GET"])
def state(request):
    """Everything one screen needs, in one call: the page polls this to stay in step
    with the other phones and the counter computer."""
    me = me_to_dict(request.user)
    me["duties"] = services.duties_of(request.user)
    return ok({"me": me, **_team(), "templates": services.wordings(), "whatsapp": whatsapp.status_view(),
               "jobs": _jobs(request)})


@api_view(["POST"])
def jobs(request):
    data = _validated(JobCreateSerializer, request.data)
    items = data.pop("items")
    job = services.create_job(request.user, data, items)
    # The thank-you goes by itself; the card is saved whether or not it could be sent.
    sent, reason = whatsapp.send_thanks(job)
    detail = _job_detail(request, job.number)
    detail["thanks"] = {"sent": sent, "reason": reason}
    return ok(detail, f"{job.code} created.", status.HTTP_201_CREATED)


@api_view(["GET", "PATCH"])
def job(request, number):
    if request.method == "PATCH":
        fields = _validated(JobUpdateSerializer, request.data, partial=True)
        services.update_job(request.user, number, fields)
        return ok(_job_detail(request, number), "Saved.")
    return ok(_job_detail(request, number))


@api_view(["PUT", "DELETE"])
def job_stage(request, number, stage):
    if stage not in STAGE_KEYS:
        return fail("Unknown step.", code=status.HTTP_404_NOT_FOUND)
    services.set_stage(request.user, number, stage, complete=request.method == "PUT")
    return ok(_job_detail(request, number))


@api_view(["POST"])
def job_message(request, number):
    """The WhatsApp button on a job card: send the customer the message now, from the shop's number."""
    kind = _validated(MessageSerializer, request.data)["kind"]
    job = Job.objects.prefetch_related("ticks").get(number=number)
    what = whatsapp.send_to_customer(job, kind, actor=request.user)
    return ok(_job_detail(request, number), f"Sent {what} to {job.customer_name}.")


@api_view(["PUT"])
def roster(request):
    data = _validated(DutySerializer, request.data)
    services.set_duty(request.user, data["user_id"], data["stage"], data["on"])
    return ok(_team())


@api_view(["POST"])
def roster_sent(request):
    data = _validated(SentSerializer, request.data)
    services.mark_work_sent(request.user, data["user_ids"])
    return ok(_team())


@api_view(["PUT", "DELETE"])
def template(request, key, lang):
    if key not in TEMPLATE_DEFAULTS or lang not in ("en", "hi"):
        return fail("Unknown message.", code=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        services.reset_wording(request.user, key, lang)
        return ok(services.wordings(), "Back to the standard wording.")
    body = _validated(WordingSerializer, request.data)["body"]
    services.save_wording(request.user, key, lang, body)
    return ok(services.wordings(), "Wording saved.")


def _whatsapp_state():
    return {"templates": services.wordings(), "whatsapp": whatsapp.status_view()}


@api_view(["POST"])
def whatsapp_submit(request, key, lang):
    if key not in whatsapp.TEMPLATE_KEYS or lang not in whatsapp.LANGS:
        return fail("Unknown message.", code=status.HTTP_404_NOT_FOUND)
    whatsapp.submit(request.user, key, lang)
    return ok(_whatsapp_state(), "Sent to WhatsApp for approval. It usually answers within a few hours.")


@api_view(["POST"])
def whatsapp_refresh(request):
    if not request.user.is_owner:
        return fail("Only the owner can check approvals.", code=status.HTTP_403_FORBIDDEN)
    whatsapp.refresh()
    return ok(_whatsapp_state())


@api_view(["POST"])
def whatsapp_replies(request):
    whatsapp.connect_incoming(request.user)
    return ok(_whatsapp_state(), "Customers who message the number will now be told where their order is.")


@api_view(["GET", "POST"])
def staff(request):
    if not request.user.is_owner:
        return fail("Only the owner can manage staff.", code=status.HTTP_403_FORBIDDEN)
    if request.method == "POST":
        data = _validated(StaffCreateSerializer, request.data)
        user = staff_services.create_staff(request.user, **data)
        return ok(staff_to_dict(user), f"{user.name} can now sign in.", status.HTTP_201_CREATED)
    return ok({"results": [staff_to_dict(u) for u in User.objects.all()[:STAFF_MAX]]})


@api_view(["PATCH"])
def staff_member(request, user_id):
    data = _validated(StaffUpdateSerializer, request.data)
    user = staff_services.update_staff(request.user, user_id, **data)
    return ok(staff_to_dict(user), "Saved.")
