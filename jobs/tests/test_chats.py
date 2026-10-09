from datetime import timedelta
from unittest import mock

import requests
from django.test import override_settings
from django.utils import timezone

from jobs import chats

from .test_api import NEW_JOB, ApiTestCase

CHAT = "0b9d6d1e-5a53-4a64-9d6c-6a1f6f6f0001"
ON = dict(CHATS_READY=True, PLATFORM_URL="https://platform.example", PLATFORM_EMAIL="shop@example.in", PLATFORM_PASSWORD="x")


def stamp(minutes):
    return (timezone.now() + timedelta(minutes=minutes)).isoformat()


def conversation(**changes):
    return {"id": CHAT, "customer_name": "Rajesh Meena", "customer_wa_phone": "919829012345", "channel": "whatsapp",
            "state": "open", "human_takeover_until": None, "window_expires_at": stamp(600), "updated_at": stamp(-3), **changes}


MESSAGES = [
    {"id": "m1", "direction": "inbound", "sender_type": "customer", "body": "hi", "media_url": "", "created_at": stamp(-5), "delivery_failed": False},
    {"id": "m2", "direction": "outbound", "sender_type": "system", "body": "Namaste!", "media_url": "", "created_at": stamp(-5), "delivery_failed": False},
]


class FakePlatform:
    """Stands in for the platform's HTTP API: answers by path, and remembers every call."""

    def __init__(self):
        self.calls, self.logins, self.expire_next, self.reply_error = [], 0, False, None

    def post(self, url, **kwargs):          # only the sign-in uses requests.post
        self.logins += 1
        return self.response(200, {"success": True, "data": {"access": f"token-{self.logins}", "refresh": "r"}})

    def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url.split("/api/v1/")[1], headers["Authorization"], kwargs.get("json")))
        if self.expire_next:
            self.expire_next = False
            return self.response(401, {"success": False, "message": "Token expired", "errors": {}})
        path = url.split("/api/v1/")[1]
        if path == "conversations/":
            return self.response(200, {"success": True, "data": {"count": 1, "results": [conversation()]}})
        if path.endswith("/reply/"):
            if self.reply_error:
                return self.response(400, {"success": False, "message": "Request failed.", "errors": {"body": [self.reply_error]}})
            return self.response(200, {"success": True, "data": {
                "id": "m3", "direction": "outbound", "sender_type": "human", "body": kwargs["json"]["body"],
                "media_url": "", "created_at": stamp(0), "delivery_failed": False}})
        if CHAT in path:
            return self.response(200, {"success": True, "data": conversation(messages=MESSAGES)})
        return self.response(404, {"success": False, "message": "Not found.", "errors": {}})

    @staticmethod
    def response(status, payload):
        res = mock.Mock(status_code=status, ok=status < 400)
        res.json.return_value = payload
        return res


@override_settings(**ON)
class ChatTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        chats._Platform._access = None
        self.platform = FakePlatform()
        for name in ("post", "request"):
            patcher = mock.patch(f"jobs.chats.requests.{name}", side_effect=getattr(self.platform, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_counter_and_the_owner_see_chats_and_others_do_not(self):
        for user, status in ((self.owner, 200), (self.aman, 200), (self.ramesh, 403)):
            self.assertEqual(self.client_for(user).get("/api/v1/chats/").status_code, status, user.name)
        self.assertIn("Order received", self.client_for(self.ramesh).get(f"/api/v1/chats/{CHAT}/").json()["message"])
        state = lambda user: self.client_for(user).get("/api/v1/state/").json()["data"]["chats"]
        self.assertEqual((state(self.aman), state(self.ramesh)), ({"ready": True, "allowed": True}, {"ready": True, "allowed": False}))

    def test_the_list_shows_each_customer_with_their_orders(self):
        number = self.new_job()["number"]
        self.tick(self.ramesh, number, "ordered")
        row = self.client_for(self.owner).get("/api/v1/chats/").json()["data"]["results"][0]
        self.assertEqual((row["name"], row["phone"], row["can_reply"], row["staff_handling"]), ("Rajesh Meena", "919829012345", True, False))
        self.assertEqual(row["orders"], [{"no": f"WS-{number}", "status": "Material ordered", "done": False}])
        self.assertNotIn("118", str(row))                                  # never the money
        self.assertEqual(self.platform.calls[0][:3], ("GET", "conversations/", "Bearer token-1"))

    def test_a_chat_opens_with_its_messages_in_plain_words(self):
        data = self.client_for(self.aman).get(f"/api/v1/chats/{CHAT}/").json()["data"]
        self.assertEqual([(m["out"], m["by"], m["body"]) for m in data["messages"]],
                         [(False, "customer", "hi"), (True, "assistant", "Namaste!")])

    def test_a_reply_goes_out_through_the_platform(self):
        res = self.client_for(self.aman).post(f"/api/v1/chats/{CHAT}/reply/", {"body": "  Your ply is in stock.  "}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["data"]["by"], res.json()["data"]["body"]), ("staff", "Your ply is in stock."))
        self.assertEqual(self.platform.calls[-1][1::2], (f"conversations/{CHAT}/reply/", {"body": "Your ply is in stock."}))
        self.assertEqual(self.client_for(self.aman).post(f"/api/v1/chats/{CHAT}/reply/", {"body": "  "}, format="json").status_code, 400)

    def test_the_platforms_reason_for_refusing_a_reply_reaches_the_screen(self):
        self.platform.reply_error = "This conversation's 24-hour reply window has closed."
        res = self.client_for(self.owner).post(f"/api/v1/chats/{CHAT}/reply/", {"body": "hello"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["message"], "This conversation's 24-hour reply window has closed.")

    def test_an_expired_sign_in_is_renewed_without_anyone_noticing(self):
        owner = self.client_for(self.owner)
        owner.get("/api/v1/chats/")
        self.platform.expire_next = True
        self.assertEqual(owner.get("/api/v1/chats/").status_code, 200)
        self.assertEqual(self.platform.logins, 2)
        self.assertEqual(self.platform.calls[-1][2], "Bearer token-2")

    def test_a_platform_that_does_not_answer_is_said_plainly(self):
        with mock.patch("jobs.chats.requests.request", side_effect=requests.ConnectionError("down")):
            res = self.client_for(self.owner).get("/api/v1/chats/")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["message"], "The chat service did not respond. Try again in a minute.")

    def test_closed_window_and_staff_handling_are_read_from_the_platforms_times(self):
        late = conversation(window_expires_at=stamp(-1), human_takeover_until=stamp(30), messages=[])
        with mock.patch.object(self.platform, "request", return_value=FakePlatform.response(200, {"success": True, "data": late})):
            with mock.patch("jobs.chats.requests.request", side_effect=self.platform.request):
                data = self.client_for(self.owner).get(f"/api/v1/chats/{CHAT}/").json()["data"]
        self.assertEqual((data["can_reply"], data["staff_handling"]), (False, True))

    @override_settings(CHATS_READY=False)
    def test_not_connected_says_so(self):
        res = self.client_for(self.owner).get("/api/v1/chats/")
        self.assertEqual((res.status_code, res.json()["message"]), (403, "Customer chats are not connected yet."))


class OtherItemTests(ApiTestCase):
    def test_an_item_outside_the_lists_is_named_by_hand(self):
        job = self.new_job(items=[{"category": "other", "brand": "Door hinges", "size": "4 inch, steel", "qty": 20}])
        self.assertEqual(job["items"], [{"cat": "other", "qty": 20, "brand": "Door hinges", "size": "4 inch, steel"}])
        self.assertContains(self.client.get(job["track"]), "Door hinges · 4 inch, steel × 20 pcs")
        self.assertNotContains(self.client.get(job["track"]), "Other:")

    def test_it_must_have_a_name(self):
        res = self.client_for(self.owner).post(
            "/api/v1/jobs/", {**NEW_JOB, "items": [{"category": "other", "brand": " ", "qty": 2}]}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("name", str(res.json()["errors"]["items"]))
