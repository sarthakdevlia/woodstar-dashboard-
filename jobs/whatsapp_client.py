"""Thin HTTP client for Gupshup's Partner API — the calls this dashboard needs, taken from the
clinic platform's client (AI-SaaS apps/integrations/whatsapp_client.py), where each was worked
out against Gupshup's reference and, where noted there, a real call.

Auth is two-step: the partner login (SD Ventures' own Gupshup partner account, one for
everything) gives a Partner Token good for 24 hours; that fetches a per-app token, which is
what sends messages. Both are cached in memory — fine for the single process this app runs as.

One call uses a different host and credential: registering where incoming messages are
delivered (the Subscription API on api.gupshup.io) wants an app-level API key, which the
partner API mints on demand and returns exactly once.
"""

import json
import threading
import time

import requests
from django.conf import settings

# Docs say 24h; refresh an hour early rather than racing the exact boundary.
PARTNER_TOKEN_TTL_SECONDS = 23 * 60 * 60
SUBSCRIPTION_API = "https://api.gupshup.io"
WEBHOOK_SECRET_HEADER = "X-Gupshup-Webhook-Secret"


class WhatsAppSendError(Exception):
    """Any provider failure: partner login, app-token fetch, or the call itself."""


def _unreachable(exc):
    """A network failure in words the desk can read; the detail goes to the log, not the screen."""
    kind = "it took too long to answer" if isinstance(exc, requests.Timeout) else "it could not be reached"
    return f"the WhatsApp provider did not respond ({kind}). Try again in a minute."


class WhatsAppClient:
    _lock = threading.Lock()
    _partner_token = None
    _partner_token_fetched_at = 0.0
    _app_tokens = {}  # app_id -> token, cached for the process lifetime

    def __init__(self, timeout=10):
        self.base_url = settings.WHATSAPP_API_BASE_URL.rstrip("/")
        self.email = settings.WHATSAPP_PARTNER_EMAIL
        self.secret = settings.WHATSAPP_PARTNER_SECRET
        self.timeout = timeout

    # ------------------------------------------------------------ sending

    def send_text_message(self, app_id, to, body):
        """A plain message. WhatsApp only delivers it to someone who wrote in the last
        24 hours. Returns the provider's message id."""
        return self._send(app_id, {"to": to, "type": "text", "text": {"body": body}}, "Message")

    def send_template(self, app_id, to, name, language_code, body_params):
        """An approved template with its {{1}}, {{2}}… filled from `body_params` — the only
        message that reaches someone who has not written first."""
        template = {
            "name": name,
            "language": {"policy": "deterministic", "code": language_code},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": str(value)} for value in body_params]}
            ],
        }
        return self._send(app_id, {"to": to, "type": "template", "template": template}, "Template")

    def _send(self, app_id, message, what):
        token = self._get_app_token(app_id)
        url = f"{self.base_url}/partner/app/{app_id}/v3/message"
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", **message}
        headers = {"Authorization": token, "Content-Type": "application/json"}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"{what} send failed: {_unreachable(exc)}") from exc
        try:
            data = response.json()
        except ValueError:
            raise WhatsAppSendError(f"Non-JSON response ({response.status_code}): {response.text[:200]}")
        if not response.ok:
            detail = data.get("error", data) if isinstance(data, dict) else data
            raise WhatsAppSendError(f"Provider rejected the message ({response.status_code}): {str(detail)[:300]}")
        try:
            return data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            raise WhatsAppSendError(f"Unexpected response from provider: {str(data)[:200]}")

    # ------------------------------------------------------------ templates

    def create_template(self, app_id, element_name, language_code, content, example, vertical="Order update"):
        """Submit a text template for Meta's approval. Returns `(provider_id, status)`;
        the status is PENDING until Meta decides."""
        token = self._get_app_token(app_id)
        data = {
            "elementName": element_name,
            "languageCode": language_code,
            "category": "UTILITY",
            "templateType": "TEXT",
            "vertical": vertical[:180],
            "content": content,
            "example": example,
            "enableSample": "true",
            # An order update is a utility message; do not let it be re-filed as marketing,
            # which costs more and can be capped.
            "allowTemplateCategoryChange": "false",
        }
        try:
            response = requests.post(f"{self.base_url}/partner/app/{app_id}/templates", data=data,
                                     headers={"Authorization": token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"Template submission failed: {_unreachable(exc)}") from exc
        payload = self._json_or_raise(response, "Template submission")
        template = payload.get("template") if isinstance(payload, dict) else None
        if not isinstance(template, dict) or not template.get("id"):
            raise WhatsAppSendError(f"Unexpected template response: {str(payload)[:200]}")
        return template["id"], str(template.get("status") or "PENDING").upper()

    def list_templates(self, app_id):
        """Every template on this WhatsApp number, each a dict with at least elementName,
        status and (when rejected) reason."""
        token = self._get_app_token(app_id)
        try:
            response = requests.get(f"{self.base_url}/partner/app/{app_id}/templates",
                                    headers={"Authorization": token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"Template list failed: {_unreachable(exc)}") from exc
        payload = self._json_or_raise(response, "Template list")
        templates = payload.get("templates") if isinstance(payload, dict) else None
        return templates if isinstance(templates, list) else []

    # ------------------------------------------------------------ incoming messages

    def create_app_api_key(self, app_id):
        """Mint the app-level API key the Subscription API requires. The value is returned
        only this once, so it is used straight away and never stored."""
        partner_token = self._get_partner_token()
        try:
            response = requests.post(f"{self.base_url}/partner/app/{app_id}/apikey",
                                     headers={"Authorization": partner_token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"App API key creation failed: {_unreachable(exc)}") from exc
        if not response.ok:
            raise WhatsAppSendError(f"App API key creation rejected ({response.status_code}): {response.text[:200]}")
        try:
            return response.json()["key"]["token"]
        except (ValueError, KeyError, TypeError):
            raise WhatsAppSendError(f"Unexpected API key response: {response.text[:200]}")

    def add_subscription(self, app_id, api_key, callback_url, tag):
        """Tell Gupshup to deliver this number's incoming messages to `callback_url`.

        version 3 delivers Meta's own payload shape, which jobs/whatsapp.py parses.
        doCheck=false because Gupshup's own URL pre-check is broken (it rejects valid URLs
        with "Invalid URL Passed" — found on the clinic side, 24 Aug 2026).
        Gupshup does not sign what it delivers, so `meta` asks it to replay a secret header
        on every delivery; it must be the bare header map, which Gupshup wraps itself.
        """
        data = {"url": callback_url, "tag": tag, "version": "3", "modes": "MESSAGE", "doCheck": "false",
                "meta": json.dumps({WEBHOOK_SECRET_HEADER: settings.WHATSAPP_WEBHOOK_SECRET})}
        headers = {"apikey": api_key, "Content-Type": "application/x-www-form-urlencoded"}
        try:
            response = requests.post(f"{SUBSCRIPTION_API}/wa/app/{app_id}/subscription", data=data,
                                     headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"Subscription registration failed: {_unreachable(exc)}") from exc
        if not response.ok:
            raise WhatsAppSendError(f"Subscription registration rejected ({response.status_code}): {response.text[:200]}")

    # ------------------------------------------------------------ auth

    @staticmethod
    def _json_or_raise(response, what):
        try:
            payload = response.json()
        except ValueError:
            raise WhatsAppSendError(f"{what}: non-JSON response ({response.status_code}): {response.text[:200]}")
        if not response.ok:
            detail = payload.get("message", payload) if isinstance(payload, dict) else payload
            raise WhatsAppSendError(f"{what} rejected ({response.status_code}): {str(detail)[:300]}")
        return payload

    def _get_partner_token(self):
        with self._lock:
            cached, fetched_at = WhatsAppClient._partner_token, WhatsAppClient._partner_token_fetched_at
            if cached and (time.monotonic() - fetched_at) < PARTNER_TOKEN_TTL_SECONDS:
                return cached
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        try:
            response = requests.post(f"{self.base_url}/partner/account/login", headers=headers, timeout=self.timeout,
                                     data={"email": self.email, "secret": self.secret})
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"Partner login failed: {_unreachable(exc)}") from exc
        if not response.ok:
            raise WhatsAppSendError(f"Partner login rejected ({response.status_code}): {response.text[:200]}")
        try:
            token = response.json()["token"]
        except (ValueError, KeyError, TypeError):
            raise WhatsAppSendError(f"Unexpected partner login response: {response.text[:200]}")
        with self._lock:
            WhatsAppClient._partner_token = token
            WhatsAppClient._partner_token_fetched_at = time.monotonic()
        return token

    def _get_app_token(self, app_id):
        with self._lock:
            cached = WhatsAppClient._app_tokens.get(app_id)
        if cached:
            return cached
        partner_token = self._get_partner_token()
        try:
            response = requests.get(f"{self.base_url}/partner/app/{app_id}/token/",
                                    headers={"Authorization": partner_token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WhatsAppSendError(f"App token fetch failed: {_unreachable(exc)}") from exc
        if not response.ok:
            raise WhatsAppSendError(f"App token fetch rejected ({response.status_code}): {response.text[:200]}")
        # Unlike the login's flat {"token": "..."}, this one nests the string one level
        # deeper: {"token": {"token": "...", ...}} (confirmed on the clinic side, 16 Aug 2026).
        try:
            token = response.json()["token"]["token"]
        except (ValueError, KeyError, TypeError):
            raise WhatsAppSendError(f"Unexpected app token response: {response.text[:200]}")
        with self._lock:
            WhatsAppClient._app_tokens[app_id] = token
        return token
