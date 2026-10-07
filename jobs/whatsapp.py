"""WhatsApp from the shop's own number.

Three things happen here, and nothing else is ever sent:
- a thank-you goes to the customer by itself when their job card is saved;
- an order update goes when someone presses the WhatsApp button on a job card;
- a customer who writes to the number is told where their order has reached.

The first two reach people who have not written first, which WhatsApp only allows for a
wording it approved in advance (a "template"), per language. This module turns the shop's
wording into a template, submits it, tracks its approval, and sends with it. The reply to an
incoming message needs no approval: the customer has just written.
"""

import hmac
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .catalog import SHOP, TEMPLATE_DEFAULTS
from .models import AuditEntry, IncomingLink, IncomingMessage, Job, WhatsAppTemplate
from .services import Denied, check_wording, wordings
from .stages import STAGE_KEYS, STAGES
from .whatsapp_client import WEBHOOK_SECRET_HEADER, WhatsAppClient, WhatsAppSendError

log = logging.getLogger(__name__)

# The messages sent through the API, and so needing approval. ("work" only fills a link.)
TEMPLATE_KEYS = ("thanks", "update")
LANGS = ("en", "hi")
BLANK = re.compile(r"\{(\w+)\}")
# What WhatsApp's reviewer sees in place of each blank.
SAMPLE = {
    "en": {"name": "Ramesh Gupta", "job": "WS-1043", "status": "Material received", "link": "Ab3dE6gH9jK2mN5p", "shop": SHOP["name"]},
    "hi": {"name": "रमेश गुप्ता", "job": "WS-1043", "status": "माल गोदाम में आ गया", "link": "Ab3dE6gH9jK2mN5p", "shop": SHOP["name"]},
}
WHAT = {"thanks": "the thank-you message", "update": "an order update"}
# A provider status → ours. Anything unknown is treated as still pending, never guessed approved.
_STATUS = {s: WhatsAppTemplate.REJECTED for s in ("REJECTED", "FAILED", "DISABLED", "DEACTIVATED", "PAUSED")}
_STATUS["APPROVED"] = WhatsAppTemplate.APPROVED

# Replies to people who write in: at most one a minute to the same phone, and "we could not
# find your order" at most twice a day, so a chatty number cannot run up a bill.
REPLY_GAP = timedelta(seconds=60)
NOT_FOUND_GAP = timedelta(hours=12)
REPLY_JOBS_MAX = 3
DELIVERED_VISIBLE = timedelta(days=14)


def ready():
    return settings.WHATSAPP_READY


def client():
    return WhatsAppClient()


def _require_ready():
    if not ready():
        raise Denied("WhatsApp sending is not set up yet.")


def _require_owner(user, action):
    if not user.is_owner:
        raise Denied(f"Only the owner can {action}.")


# ---------------------------------------------------------------- wording → template

def to_numbered(text):
    """The shop's wording as WhatsApp wants it: `(content, params)`, each {name} turned into
    {{1}}, {{2}}… in the order it first appears. {link} becomes the tracking address with the
    order's own code as the blank, so the reviewer sees a fixed, real web address."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    params = []

    def number(match):
        name = match.group(1)
        if name not in params:
            params.append(name)
        blank = "{{%d}}" % (params.index(name) + 1)
        return f"{settings.PUBLIC_URL}/t/{blank}/" if name == "link" else blank

    return BLANK.sub(number, text), ",".join(params)


def fill(content, params, values):
    for number, name in enumerate([p for p in params.split(",") if p], start=1):
        content = content.replace("{{%d}}" % number, str(values[name]))
    return content


def _param(value):
    """WhatsApp rejects a blank's value with a line break, a tab or a run of spaces."""
    return " ".join(str(value).split())


def _latest():
    latest = {}
    for row in WhatsAppTemplate.objects.order_by("version"):
        latest[(row.key, row.lang)] = row
    return latest


def approved_row(key, lang):
    """The template to send with: the newest approved wording in the customer's language,
    else in the other one — a message in English beats none."""
    approved = {}
    for row in WhatsAppTemplate.objects.filter(key=key, status=WhatsAppTemplate.APPROVED).order_by("version"):
        approved[row.lang] = row
    return approved.get(lang) or next(iter(approved.values()), None)


def submit(actor, key, lang):
    """Send the current wording of one message, in one language, for WhatsApp's approval, as
    a new template. The approved one keeps being used until this one is approved."""
    _require_owner(actor, "submit a message to WhatsApp")
    _require_ready()
    text = wordings()[key][lang]
    problem = check_wording(key, text)
    if problem:
        raise Denied(problem)
    content, params = to_numbered(text)
    current = _latest().get((key, lang))
    if current is not None and current.status == WhatsAppTemplate.PENDING:
        raise Denied("The last wording is still waiting for WhatsApp's approval. Change it once that is decided.")
    if current is not None and current.content == content and current.status != WhatsAppTemplate.FAILED:
        raise Denied("That wording has already been submitted.")
    if current is not None and current.status == WhatsAppTemplate.FAILED:
        # The provider never took that one, so its name is still free: try again under it.
        row = current
        row.content, row.params, row.status, row.reason = content, params, WhatsAppTemplate.PENDING, ""
    else:
        version = (current.version if current else 0) + 1
        row = WhatsAppTemplate(key=key, lang=lang, version=version, content=content, params=params,
                               element_name=f"woodstar_{key}_{lang}_v{version}")
    row.checked_at = timezone.now()
    try:
        row.provider_id, _ = client().create_template(
            settings.WHATSAPP_APP_ID, row.element_name, lang, content, fill(content, params, SAMPLE[lang]),
            vertical=TEMPLATE_DEFAULTS[key]["title"])
    except WhatsAppSendError as exc:
        log.warning("Template %s could not be submitted: %s", row.element_name, exc)
        row.status, row.reason = WhatsAppTemplate.FAILED, str(exc)[:500]
        row.save()
        raise Denied(f"WhatsApp did not accept the submission: {str(exc)[:200]}") from exc
    row.save()
    return row


def refresh():
    """Ask the provider where each submitted template stands."""
    rows = list(WhatsAppTemplate.objects.exclude(status=WhatsAppTemplate.FAILED))
    if not rows or not ready():
        return
    try:
        listed = client().list_templates(settings.WHATSAPP_APP_ID)
    except WhatsAppSendError:
        log.exception("Could not list WhatsApp templates")
        return
    by_name = {t.get("elementName"): t for t in listed if isinstance(t, dict)}
    for row in rows:
        found = by_name.get(row.element_name)
        if found is None:
            continue
        row.status = _STATUS.get(str(found.get("status") or "").upper(), WhatsAppTemplate.PENDING)
        row.reason = str(found.get("reason") or "")[:500] if row.status == WhatsAppTemplate.REJECTED else ""
        row.checked_at = timezone.now()
        row.save(update_fields=["status", "reason", "checked_at"])


def status_view():
    """What the WhatsApp messages screen shows: per message and language, where the newest
    submitted wording stands, whether an approved one is in use, and whether the wording has
    been edited since it was submitted."""
    latest, current = _latest(), wordings()
    messages = {}
    for key in TEMPLATE_KEYS:
        messages[key] = {}
        for lang in LANGS:
            row = latest.get((key, lang))
            in_use = approved_row(key, lang)
            messages[key][lang] = {
                "status": row.status if row else "not_submitted",
                "reason": row.reason if row else "",
                "version": row.version if row else 0,
                "in_use": bool(in_use and in_use.lang == lang),
                "edited": bool(row) and to_numbered(current[key][lang])[0] != row.content,
            }
    return {"ready": ready(), "can_reply": bool(settings.WHATSAPP_WEBHOOK_SECRET),
            "replies_on": IncomingLink.objects.exists(), "messages": messages}


# ---------------------------------------------------------------- sending to the customer

def _reached(job):
    """The step the order has reached, as its stage dict."""
    done = {t.stage for t in job.ticks.all()}
    return next((s for s in reversed(STAGES) if s["key"] in done), STAGES[0])


def _values(job, lang):
    stage = _reached(job)
    return {"name": job.customer_name, "job": job.code, "link": job.track_token, "shop": SHOP["name"],
            "status": stage["labelHi"] if lang == "hi" else stage["label"]}


def send_to_customer(job, kind, actor=None):
    """Send `kind` ("thanks" or "update") to the job's customer from the shop's number, and
    write it to the job's audit trail. Raises Denied with a reason the desk can act on."""
    _require_ready()
    who, who_name = (actor, actor.name) if actor else (None, "The dashboard")
    row = approved_row(kind, job.lang)
    if row is None and WhatsAppTemplate.objects.filter(key=kind, status=WhatsAppTemplate.PENDING).exists():
        refresh()  # it may have been approved since anyone last looked
        row = approved_row(kind, job.lang)
    if row is None:
        raise Denied("WhatsApp has not approved this message yet. The owner can check it under WhatsApp messages.")
    values = _values(job, row.lang)
    what = WHAT[kind] + (f" ({_reached(job)['label']})" if kind == "update" else "")
    try:
        client().send_template(settings.WHATSAPP_APP_ID, "91" + job.customer_phone, row.element_name, row.lang,
                               [_param(values[name]) for name in row.params.split(",") if name])
    except WhatsAppSendError as exc:
        log.warning("%s to %s not sent: %s", kind, job.code, exc)
        AuditEntry.objects.create(job=job, actor=who, actor_name=who_name, action=AuditEntry.MESSAGE_FAILED,
                                  detail=f"{what}: {str(exc)[:180]}")
        raise Denied(f"WhatsApp did not send it: {str(exc)[:200]}") from exc
    AuditEntry.objects.create(job=job, actor=who, actor_name=who_name, action=AuditEntry.MESSAGED, detail=what)
    return what


def send_thanks(job):
    """Called once, when a job card is saved. Never raises: a card must be saved whether or
    not the message goes. Returns `(sent, reason)`."""
    if not ready():
        return False, "WhatsApp sending is not set up yet."
    try:
        send_to_customer(job, "thanks")
    except Denied as exc:
        return False, str(exc)
    except Exception:  # noqa: BLE001 — a surprise here must not lose the job card
        log.exception("Thank-you for %s failed", job.code)
        return False, "Something went wrong while sending."
    return True, ""


# ---------------------------------------------------------------- customers who write in

def connect_incoming(actor):
    """Tell the shop's number to deliver its incoming messages to this dashboard. Do this
    only for a number used by this shop alone: every message to it will be answered from here."""
    _require_owner(actor, "switch on replies")
    _require_ready()
    if not settings.WHATSAPP_WEBHOOK_SECRET:
        raise Denied("Set WHATSAPP_WEBHOOK_SECRET in the service's environment first.")
    callback = f"{settings.PUBLIC_URL}/webhooks/whatsapp/"
    try:
        api = client()
        api.add_subscription(settings.WHATSAPP_APP_ID, api.create_app_api_key(settings.WHATSAPP_APP_ID), callback,
                             tag="woodstar-dashboard")
    except WhatsAppSendError as exc:
        log.warning("Could not register for incoming messages: %s", exc)
        raise Denied(f"Could not switch replies on: {str(exc)[:200]}") from exc
    IncomingLink.objects.create(callback_url=callback, created_by=actor)


def is_from_provider(request):
    """Gupshup does not sign what it delivers; it replays the secret header we registered."""
    secret, given = settings.WHATSAPP_WEBHOOK_SECRET, request.headers.get(WEBHOOK_SECRET_HEADER)
    return bool(secret) and given is not None and hmac.compare_digest(given, secret)


def extract_messages(payload):
    """Incoming customer messages in a delivery (Meta's shape, which Gupshup's v3 passes on),
    as `[{id, phone, text}]`. Receipts, anything for another number and anything malformed
    give an empty list: a webhook must accept what it does not care about."""
    try:
        if payload.get("gs_app_id") not in (None, "", settings.WHATSAPP_APP_ID):
            return []
        raw_messages = payload["entry"][0]["changes"][0]["value"].get("messages") or []
    except (AttributeError, KeyError, IndexError, TypeError):
        return []
    messages = []
    for raw in raw_messages:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("from"):
            continue
        text = (raw.get("text") or {}).get("body", "") if isinstance(raw.get("text"), dict) else ""
        messages.append({"id": str(raw["id"]), "phone": re.sub(r"\D", "", str(raw["from"])), "text": str(text)})
    return messages


def _day(d):
    return f"{d:%a}, {d.day} {d:%b}"


def _job_lines(job, hindi):
    ticks = {t.stage: t for t in job.ticks.all()}
    reached = _reached(job)
    label = reached["labelHi"] if hindi else reached["label"]
    link = f"{settings.PUBLIC_URL}/t/{job.track_token}/"
    if len(ticks) == len(STAGE_KEYS):
        on = _day(timezone.localtime(ticks[STAGE_KEYS[-1]].done_at))
        return f"*{job.code}* — {label} ✓ ({on})\n{link}"
    following = next(s for s in STAGES if s["key"] not in ticks)
    if hindi:
        return f"*{job.code}* — {label} ✓\nअगला कदम: {following['labelHi']} · वादा: {_day(job.due)}\n{link}"
    return f"*{job.code}* — {label} ✓\nNext: {following['label']} · promised by {_day(job.due)}\n{link}"


def reply_for(phone):
    """`(kind, text)` for a customer who wrote from `phone`: where their orders have reached,
    or that none was found on that number."""
    recent = timezone.now() - DELIVERED_VISIBLE
    jobs = []
    for job in Job.objects.filter(customer_phone=phone[-10:]).prefetch_related("ticks").order_by("-number"):
        delivered = next((t for t in job.ticks.all() if t.stage == STAGE_KEYS[-1]), None)
        if delivered is None or delivered.done_at >= recent:
            jobs.append(job)
    if not jobs:
        return "not_found", (
            f"Namaste! We could not find an order on this number at {SHOP['name']}. If you ordered with another "
            f"number, please message us from that one, or call {SHOP['phone']}.\n\n"
            f"नमस्ते! {SHOP['name']} में इस नंबर पर कोई ऑर्डर नहीं मिला। अगर आपने दूसरे नंबर से ऑर्डर किया था तो उसी "
            f"नंबर से संदेश भेजें, या {SHOP['phone']} पर कॉल करें।")
    hindi = jobs[0].lang == "hi"
    lines = "\n\n".join(_job_lines(job, hindi) for job in jobs[:REPLY_JOBS_MAX])
    if hindi:
        return "status", f"नमस्ते {jobs[0].customer_name}, {SHOP['name']} में आपका ऑर्डर:\n\n{lines}\n\nकिसी भी मदद के लिए {SHOP['phone']} पर कॉल करें।"
    return "status", f"Namaste {jobs[0].customer_name}, here is your order at {SHOP['name']}:\n\n{lines}\n\nFor anything else, call {SHOP['phone']}."


def handle_incoming(payload):
    """Answer each new customer message in a delivery. Returns how many replies went."""
    sent = 0
    if not ready():
        return sent
    for message in extract_messages(payload):
        phone, now = message["phone"], timezone.now()
        record, new = IncomingMessage.objects.get_or_create(
            wa_message_id=message["id"][:200], defaults={"phone": phone[:20], "text": message["text"][:500]})
        if not new:
            continue  # Gupshup delivered this one before
        earlier = IncomingMessage.objects.filter(phone=phone[:20]).exclude(pk=record.pk)
        if earlier.filter(received_at__gte=now - REPLY_GAP).exclude(replied="").exists():
            continue
        kind, text = reply_for(phone)
        if kind == "not_found" and earlier.filter(received_at__gte=now - NOT_FOUND_GAP, replied="not_found").exists():
            continue
        try:
            client().send_text_message(settings.WHATSAPP_APP_ID, phone, text)
        except WhatsAppSendError as exc:
            log.warning("Reply to an incoming message was not sent: %s", exc)
            continue
        record.replied = kind
        record.save(update_fields=["replied"])
        sent += 1
    return sent
