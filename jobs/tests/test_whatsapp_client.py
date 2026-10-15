from unittest import mock

from django.test import SimpleTestCase, override_settings

from jobs.whatsapp_client import WhatsAppClient, WhatsAppSendError


def _ok(payload):
    return mock.Mock(ok=True, json=lambda: payload)


class UniversalTokenTests(SimpleTestCase):
    """Gupshup retires the partner login on 1 Apr 2027. With a Universal Token set, no call may
    fall back to the login; without one, nothing about a call may change."""

    @override_settings(GUPSHUP_UNIVERSAL_TOKEN="UT")
    @mock.patch("jobs.whatsapp_client.requests.post")
    @mock.patch.object(WhatsAppClient, "_get_partner_token", side_effect=AssertionError("legacy login"))
    @mock.patch.object(WhatsAppClient, "_get_app_token", side_effect=AssertionError("legacy app token"))
    def test_with_the_token_a_send_uses_it_and_never_logs_in(self, _app, _partner, post):
        post.return_value = _ok({"messages": [{"id": "wamid.1"}]})
        WhatsAppClient().send_text_message("app-1", "919000000001", "hi")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer UT")

    @override_settings(GUPSHUP_UNIVERSAL_TOKEN="")
    @mock.patch("jobs.whatsapp_client.requests.post")
    @mock.patch.object(WhatsAppClient, "_get_app_token", return_value="APPTOKEN")
    def test_without_it_the_partner_login_is_used_as_before(self, _app, post):
        post.return_value = _ok({"messages": [{"id": "wamid.1"}]})
        WhatsAppClient().send_text_message("app-1", "919000000001", "hi")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "APPTOKEN")

    @override_settings(GUPSHUP_UNIVERSAL_TOKEN="UT")
    @mock.patch("jobs.whatsapp_client.requests.post")
    def test_the_subscription_takes_the_token_when_there_is_no_key(self, post):
        post.return_value = _ok({"status": "success"})
        WhatsAppClient().add_subscription("app-1", None, "https://dash.example/webhooks/whatsapp/", tag="t")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer UT")
        self.assertNotIn("apikey", headers)

    @override_settings(GUPSHUP_UNIVERSAL_TOKEN="")
    def test_a_subscription_with_neither_key_nor_token_is_refused(self):
        with self.assertRaises(WhatsAppSendError):
            WhatsAppClient().add_subscription("app-1", None, "https://dash.example/webhooks/whatsapp/", tag="t")
