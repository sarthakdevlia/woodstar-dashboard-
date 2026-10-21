from django.utils import timezone
from rest_framework import serializers

from .catalog import CATEGORY_BY_KEY, CATEGORY_KEYS
from .models import Job, JobItem
from .services import audit_text
from .stages import LANG_CHOICES, ROLE_CHOICES, STAGE_KEYS

AUDIT_LIMIT = 100
PHONE = r"^[6-9]\d{9}$"
PHONE_ERROR = {"invalid": "Enter a 10-digit mobile number."}


class ItemSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=CATEGORY_KEYS)
    brand = serializers.CharField(max_length=60, required=False, allow_blank=True)
    thickness = serializers.CharField(max_length=60, required=False, allow_blank=True)
    size = serializers.CharField(max_length=60, required=False, allow_blank=True)
    pack = serializers.CharField(max_length=60, required=False, allow_blank=True)
    qty = serializers.IntegerField(min_value=1, max_value=100000)
    # Known when the order is taken: already in the godown, or to be bought. Blank: not checked.
    source = serializers.ChoiceField(choices=[c for c, _ in JobItem.SOURCE_CHOICES], required=False, allow_blank=True)


def _named(items):
    """An item outside the lists has nothing but its name to say what it is."""
    for item in items:
        if item["category"] == "other" and not (item.get("brand") or "").strip():
            raise serializers.ValidationError("Give every \"Other\" item a name.")
    return items


class JobFieldsMixin:
    def validate(self, attrs):
        for name, value in attrs.items():
            if isinstance(value, str):
                attrs[name] = value.strip()
        return attrs


class JobCreateSerializer(JobFieldsMixin, serializers.ModelSerializer):
    customer_phone = serializers.RegexField(PHONE, error_messages=PHONE_ERROR)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    advance = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    items = ItemSerializer(many=True, allow_empty=False, max_length=50)

    def validate_items(self, items):
        return _named(items)

    class Meta:
        model = Job
        fields = ["customer_name", "customer_phone", "site", "contractor", "mode", "due", "notes", "lang",
                  "amount", "advance", "items"]


class JobUpdateSerializer(JobFieldsMixin, serializers.ModelSerializer):
    customer_phone = serializers.RegexField(PHONE, error_messages=PHONE_ERROR, required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    advance = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

    class Meta:
        model = Job
        fields = ["customer_name", "customer_phone", "site", "contractor", "mode", "due", "notes", "lang", "amount", "advance"]


def _stamp(dt):
    return timezone.localtime(dt).isoformat()


def _purchase_of(item):
    """The purchase bringing this item in, as the order shows it; None before one is placed."""
    p = item.purchase
    if p is None:
        return None
    return {"id": p.id, "code": p.code, "supplier": p.supplier.name, "status": p.status,
            "expected": p.expected.isoformat() if p.expected else None}


def job_to_dict(job, user, detail=False):
    ticks = {t.stage: {"by": t.done_by_name, "at": _stamp(t.done_at)} for t in job.ticks.all()}
    data = {
        "no": job.code,
        "number": job.number,
        "customer": {"name": job.customer_name, "phone": job.customer_phone},
        "site": job.site,
        "contractor": job.contractor,
        "mode": job.mode,
        "due": job.due.isoformat(),
        "notes": job.notes,
        "lang": job.lang,
        "items": [
            {"id": i.id, "cat": i.category, "qty": i.qty, "source": i.source, "purchase": _purchase_of(i),
             **{f: getattr(i, f) for f in CATEGORY_BY_KEY[i.category]["fields"] if f != "qty"}}
            for i in job.items.all()
        ],
        "stages": {key: ticks.get(key) for key in STAGE_KEYS},
        "createdAt": _stamp(job.created_at),
        "track": f"/t/{job.track_token}/",
    }
    if user.is_owner:
        data["amount"] = float(job.amount)
        data["advance"] = float(job.advance)
    if detail:
        entries = list(job.audit.all().order_by("-created_at", "-id")[:AUDIT_LIMIT])
        data["log"] = [{"txt": f"{e.actor_name} {audit_text(e)}", "at": _stamp(e.created_at)} for e in reversed(entries)]
    return data


class DutySerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    stage = serializers.ChoiceField(choices=STAGE_KEYS)
    on = serializers.BooleanField()


class SentSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=200)


class MessageSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["thanks", "update"])


class SourceSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=[c for c, _ in JobItem.SOURCE_CHOICES], allow_blank=True)


class SupplierSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, trim_whitespace=True)
    phone = serializers.RegexField(PHONE, error_messages=PHONE_ERROR, required=False, allow_blank=True, default="")


class PurchaseSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField(min_value=1)
    item_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=100)
    expected = serializers.DateField(required=False, allow_null=True, default=None)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="", trim_whitespace=True)


class ReplySerializer(serializers.Serializer):
    # 4096 is WhatsApp's own limit for a text message.
    body = serializers.CharField(max_length=4096, trim_whitespace=True)


class WordingSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000, trim_whitespace=True)


class StaffCreateSerializer(serializers.Serializer):
    username = serializers.RegexField(r"^[a-zA-Z0-9_.-]{3,30}$", error_messages={
        "invalid": "3–30 letters, numbers, dots, dashes or underscores."})
    name = serializers.CharField(max_length=80, trim_whitespace=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES)
    phone = serializers.RegexField(PHONE, error_messages=PHONE_ERROR, required=False, allow_blank=True)
    lang = serializers.ChoiceField(choices=LANG_CHOICES, required=False)
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False)


class StaffUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, trim_whitespace=True, required=False)
    role = serializers.ChoiceField(choices=ROLE_CHOICES, required=False)
    phone = serializers.RegexField(PHONE, error_messages=PHONE_ERROR, required=False, allow_blank=True)
    lang = serializers.ChoiceField(choices=LANG_CHOICES, required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False, required=False)


def staff_to_dict(user):
    return {"id": user.id, "username": user.username, "name": user.name, "role": user.role, "title": user.title,
            "phone": user.phone, "lang": user.lang, "is_active": user.is_active}


def worker_to_dict(user):
    return {"id": user.id, "name": user.name, "phone": user.phone, "lang": user.lang}


def me_to_dict(user):
    return {"id": user.id, "name": user.name, "role": user.role, "title": user.title, "is_owner": user.is_owner}
