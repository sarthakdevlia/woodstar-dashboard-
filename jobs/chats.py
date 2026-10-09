"""Customer chats.

The shop's WhatsApp number is answered on the SD Ventures platform, so that is where its
conversations live. This dashboard does not keep a second copy: it reads them, and sends a
person's reply, through the platform's own API (`/api/v1/conversations/`), signed in as one of
the shop's users there. A reply sent from here is recorded on the platform like any other
staff reply, and quietens the assistant on that chat for a while, exactly as it does there.
"""

import logging
import threading
from datetime import datetime

import requests
from django.conf import settings
from django.utils import timezone

from .models import Job
from .stages import STAGE_KEYS, STAGES

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
LIST_SIZE = 50
ORDERS_PER_CHAT = 5
# Who wrote a message, in the words the screen uses.
_BY = {"customer": "customer", "ai": "assistant", "system": "assistant", "human": "staff"}


class ChatError(Exception):
    """Something the person at the screen should be told, in words they can act on."""


def ready():
    return settings.CHATS_READY


class _Platform:
    """The signed-in session with the platform. Access tokens last minutes, so one is kept
    and simply fetched again when the platform says it has expired."""

    _lock = threading.Lock()
    _access = None

    def _sign_in(self):
        try:
            response = requests.post(f"{settings.PLATFORM_URL}/api/v1/auth/login/", timeout=TIMEOUT_SECONDS,
                                     json={"email": settings.PLATFORM_EMAIL, "password": settings.PLATFORM_PASSWORD})
            token = response.json()["data"]["access"] if response.ok else None
        except (requests.RequestException, ValueError, KeyError, TypeError):
            log.warning("Could not reach the platform to sign in", exc_info=True)
            raise ChatError("The chat service did not respond. Try again in a minute.")
        if not token:
            log.warning("Platform sign-in refused (%s)", response.status_code)
            raise ChatError("The chat service refused the dashboard's sign-in. Ask SD Ventures to check its sign-in details.")
        with self._lock:
            _Platform._access = token
        return token

    def call(self, method, path, **kwargs):
        with self._lock:
            token = _Platform._access
        for attempt in (1, 2):
            token = token or self._sign_in()
            try:
                response = requests.request(method, f"{settings.PLATFORM_URL}/api/v1/{path}", timeout=TIMEOUT_SECONDS,
                                            headers={"Authorization": f"Bearer {token}"}, **kwargs)
            except requests.RequestException:
                log.warning("Platform call failed: %s %s", method, path, exc_info=True)
                raise ChatError("The chat service did not respond. Try again in a minute.")
            if response.status_code == 401 and attempt == 1:
                token = None                              # expired: sign in afresh, once
                continue
            break
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code == 404:
            raise ChatError("That chat was not found.")
        if not response.ok or not payload.get("success"):
            raise ChatError(_reason(payload) or "The chat service could not do that. Try again.")
        return payload.get("data")


def _reason(payload):
    """The platform's own explanation of a refusal, e.g. that the 24-hour reply window closed."""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, dict):
        for value in errors.values():
            value = value[0] if isinstance(value, list) and value else value
            if isinstance(value, str) and value:
                return value
    message = payload.get("message") if isinstance(payload, dict) else ""
    return message if message and message != "Request failed." else ""


def _orders_for(phones):
    """{last ten digits: that customer's recent orders, newest first}, in one query."""
    tails = {p[-10:] for p in phones if len(p) >= 10}
    found = {}
    for job in Job.objects.filter(customer_phone__in=tails).prefetch_related("ticks").order_by("-number"):
        rows = found.setdefault(job.customer_phone, [])
        if len(rows) < ORDERS_PER_CHAT:
            done = {t.stage for t in job.ticks.all()}
            reached = next((s for s in reversed(STAGES) if s["key"] in done), STAGES[0])
            rows.append({"no": job.code, "status": reached["label"], "done": len(done) == len(STAGE_KEYS)})
    return found


def _still_ahead(stamp):
    """Whether a time the platform sent (ISO text, any offset) is still in the future."""
    try:
        return bool(stamp) and datetime.fromisoformat(str(stamp)) > timezone.now()
    except (ValueError, TypeError):
        return False


def _chat(row, orders):
    phone = "".join(c for c in str(row.get("customer_wa_phone") or "") if c.isdigit())
    return {
        "id": row["id"], "name": row.get("customer_name") or "", "phone": phone, "updated_at": row.get("updated_at"),
        # WhatsApp lets a business write freely only for 24 hours after the customer's last message.
        "can_reply": _still_ahead(row.get("window_expires_at")),
        "staff_handling": _still_ahead(row.get("human_takeover_until")),
        "orders": orders.get(phone[-10:], []),
    }


def _message(row):
    return {
        "id": row["id"], "out": row.get("direction") == "outbound", "by": _BY.get(row.get("sender_type"), "assistant"),
        "body": row.get("body") or "", "media": row.get("media_url") or "", "at": row.get("created_at"),
        "failed": bool(row.get("delivery_failed")),
    }


def list_chats():
    data = _Platform().call("GET", "conversations/", params={"page_size": LIST_SIZE}) or {}
    rows = [r for r in data.get("results", []) if isinstance(r, dict) and r.get("id")]
    orders = _orders_for(["".join(c for c in str(r.get("customer_wa_phone") or "") if c.isdigit()) for r in rows])
    return [_chat(r, orders) for r in rows]


def get_chat(chat_id):
    row = _Platform().call("GET", f"conversations/{chat_id}/") or {}
    orders = _orders_for(["".join(c for c in str(row.get("customer_wa_phone") or "") if c.isdigit())])
    chat = _chat(row, orders)
    chat["messages"] = [_message(m) for m in row.get("messages", []) if isinstance(m, dict)]
    return chat


def send_reply(chat_id, body):
    return _message(_Platform().call("POST", f"conversations/{chat_id}/reply/", json={"body": body}) or {})
