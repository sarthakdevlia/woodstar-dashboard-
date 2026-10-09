import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from jobs import whatsapp
from jobs.models import IncomingMessage, Job, WhatsAppTemplate
from jobs.whatsapp_client import WhatsAppSendError

from .test_api import NEW_JOB, ApiTestCase

SECRET = "hook-secret"
ON = dict(WHATSAPP_READY=True, WHATSAPP_APP_ID="app-1", WHATSAPP_WEBHOOK_SECRET=SECRET,
          PUBLIC_URL="https://dashboard.example.in")


class FakeGupshup:
    """Stands in for Gupshup: records what it was asked to do."""

    def __init__(self):
        self.sent, self.submitted, self.listed, self.subscriptions, self.fail = [], [], [], [], None

    def create_template(self, app_id, element_name, language_code, content, example, vertical=""):
        self.submitted.append({"name": element_name, "lang": language_code, "content": content, "example": example})
        return f"id-{element_name}", "PENDING"

    def list_templates(self, app_id):
        return self.listed

    def send_template(self, app_id, to, name, language_code, body_params):
        if self.fail:
            raise WhatsAppSendError(self.fail)
        self.sent.append({"to": to, "template": name, "lang": language_code, "params": body_params})
        return "wamid.template"

    def send_text_message(self, app_id, to, body):
        if self.fail:
            raise WhatsAppSendError(self.fail)
        self.sent.append({"to": to, "text": body})
        return "wamid.text"

    def create_app_api_key(self, app_id):
        return "one-time-key"

    def add_subscription(self, app_id, api_key, callback_url, tag):
        self.subscriptions.append({"key": api_key, "url": callback_url, "tag": tag})


@override_settings(**ON)
class WhatsAppTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.gupshup = FakeGupshup()
        patcher = mock.patch("jobs.whatsapp.client", return_value=self.gupshup)
        patcher.start()
        self.addCleanup(patcher.stop)

    def approve(self, key, lang="en"):
        """Submit the standard wording and have WhatsApp approve it."""
        row = whatsapp.submit(self.owner, key, lang)
        self.gupshup.listed.append({"elementName": row.element_name, "status": "APPROVED"})
        whatsapp.refresh()
        return row

    def press(self, user, number, kind="update"):
        return self.client_for(user).post(f"/api/v1/jobs/{number}/messages/", {"kind": kind}, format="json")

    def deliver(self, phone, text="hi", message_id="wamid.in.1", secret=SECRET, app="app-1"):
        payload = {"gs_app_id": app, "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "1"}, "messages": [{"id": message_id, "from": phone, "type": "text", "text": {"body": text}}],
        }}]}]}
        headers = {"HTTP_X_GUPSHUP_WEBHOOK_SECRET": secret} if secret else {}
        return self.client.post("/webhooks/whatsapp/", json.dumps(payload), content_type="application/json", **headers)


class WordingToTemplateTests(WhatsAppTestCase):
    def test_blanks_are_numbered_in_order_and_the_link_keeps_a_fixed_address(self):
        content, params = whatsapp.to_numbered("Namaste {name}, order {order} is now {status}.  Track: {link} Thank you {name}.")
        self.assertEqual(content, "Namaste {{1}}, order {{2}} is now {{3}}. Track: https://dashboard.example.in/t/{{4}}/ Thank you {{1}}.")
        self.assertEqual(params, "name,order,status,link")
        filled = whatsapp.fill(content, params, {"name": "Asha", "order": "WS-1", "status": "Dispatch", "link": "tok"})
        self.assertEqual(filled, "Namaste Asha, order WS-1 is now Dispatch. Track: https://dashboard.example.in/t/tok/ Thank you Asha.")


class ApprovalTests(WhatsAppTestCase):
    def test_owner_submits_and_approval_is_tracked(self):
        res = self.client_for(self.owner).post("/api/v1/whatsapp/templates/update/en/submit/")
        self.assertEqual(res.status_code, 200, res.content)
        sent = self.gupshup.submitted[0]
        self.assertEqual(sent["name"], "woodstar_update_en_v1")
        self.assertIn("at {{3}} is now: {{4}}. Track it any time: https://dashboard.example.in/t/{{5}}/ Thank you.", sent["content"])
        self.assertIn("Ramesh Gupta, your order WS-1043", sent["example"])
        state = res.json()["data"]["whatsapp"]["messages"]["update"]
        self.assertEqual((state["en"]["status"], state["hi"]["status"]), ("pending", "not_submitted"))

        self.gupshup.listed = [{"elementName": "woodstar_update_en_v1", "status": "APPROVED"}]
        res = self.client_for(self.owner).post("/api/v1/whatsapp/refresh/")
        self.assertEqual(res.json()["data"]["whatsapp"]["messages"]["update"]["en"]["status"], "approved")

    def test_only_owner_submits_and_not_twice_while_waiting(self):
        self.assertEqual(self.client_for(self.aman).post("/api/v1/whatsapp/templates/update/en/submit/").status_code, 403)
        owner = self.client_for(self.owner)
        owner.post("/api/v1/whatsapp/templates/update/en/submit/")
        res = owner.post("/api/v1/whatsapp/templates/update/en/submit/")
        self.assertIn("still waiting", res.json()["message"])
        self.assertEqual(len(self.gupshup.submitted), 1)

    def test_rejection_reason_is_shown(self):
        whatsapp.submit(self.owner, "thanks", "hi")
        self.gupshup.listed = [{"elementName": "woodstar_thanks_hi_v1", "status": "REJECTED", "reason": "Looks promotional"}]
        whatsapp.refresh()
        view = whatsapp.status_view()["messages"]["thanks"]["hi"]
        self.assertEqual((view["status"], view["reason"]), ("rejected", "Looks promotional"))

    def test_changed_wording_is_a_new_version_and_the_approved_one_stays_in_use(self):
        self.approve("update")
        new = "Namaste {name}, order {order} has reached: {status}. Follow it here: {link} Thank you."
        self.client_for(self.owner).put("/api/v1/templates/update/en/", {"body": new}, format="json")
        self.assertTrue(whatsapp.status_view()["messages"]["update"]["en"]["edited"])
        row = whatsapp.submit(self.owner, "update", "en")
        self.assertEqual((row.version, row.element_name, row.status), (2, "woodstar_update_en_v2", "pending"))
        self.assertEqual(whatsapp.approved_row("update", "en").version, 1)
        self.assertTrue(whatsapp.status_view()["messages"]["update"]["en"]["in_use"])

    def test_a_submission_the_provider_refuses_is_recorded(self):
        with mock.patch.object(self.gupshup, "create_template", side_effect=WhatsAppSendError("name already exists")):
            res = self.client_for(self.owner).post("/api/v1/whatsapp/templates/update/en/submit/")
        self.assertEqual(res.status_code, 403)
        self.assertIn("name already exists", res.json()["message"])
        self.assertEqual(WhatsAppTemplate.objects.get().status, "failed")
        # trying again uses the same name rather than climbing to v2, v3…
        self.client_for(self.owner).post("/api/v1/whatsapp/templates/update/en/submit/")
        row = WhatsAppTemplate.objects.get()
        self.assertEqual((row.element_name, row.status, row.reason), ("woodstar_update_en_v1", "pending", ""))


class UpdateButtonTests(WhatsAppTestCase):
    def test_pressing_the_button_sends_the_current_status_from_the_shops_number(self):
        self.approve("update")
        job = self.new_job(self.owner)
        self.gupshup.sent.clear()
        self.tick(self.ramesh, job["number"], "ordered")
        self.assertEqual(self.gupshup.sent, [])                 # ticking a step sends nothing by itself
        res = self.press(self.ramesh, job["number"])
        self.assertEqual(res.status_code, 200, res.content)
        token = Job.objects.get(number=job["number"]).track_token
        self.assertEqual(self.gupshup.sent, [{
            "to": "919829012345", "template": "woodstar_update_en_v1", "lang": "en",
            "params": ["Rajesh Meena", f"WS-{job['number']}", "WoodStar Ply Lam", "Material ordered", token]}])
        self.assertEqual(res.json()["data"]["log"][-1]["txt"], "Ramesh sent an order update (Material ordered) on WhatsApp")

    def test_not_approved_yet_says_so_and_sends_nothing(self):
        whatsapp.submit(self.owner, "update", "en")
        number = self.new_job()["number"]
        res = self.press(self.owner, number)
        self.assertEqual(res.status_code, 403)
        self.assertIn("has not approved", res.json()["message"])
        self.assertEqual(self.gupshup.sent, [])

    def test_hindi_customer_gets_hindi_and_english_if_only_that_is_approved(self):
        self.approve("update", "en")
        number = self.new_job(lang="hi")["number"]
        self.press(self.owner, number)
        self.assertEqual((self.gupshup.sent[-1]["lang"], self.gupshup.sent[-1]["params"][3]), ("en", "Order received"))
        self.approve("update", "hi")
        self.press(self.owner, number)
        self.assertEqual((self.gupshup.sent[-1]["lang"], self.gupshup.sent[-1]["params"][3]), ("hi", "ऑर्डर मिल गया"))

    def test_a_failed_send_is_reported_and_kept_in_the_audit_trail(self):
        self.approve("update")
        number = self.new_job()["number"]
        self.gupshup.fail = "131026 message undeliverable"
        res = self.press(self.owner, number)
        self.assertEqual(res.status_code, 403)
        self.assertIn("131026", res.json()["message"])
        log = self.client_for(self.owner).get(f"/api/v1/jobs/{number}/").json()["data"]["log"]
        self.assertIn("could not send an order update (Order received): 131026", log[-1]["txt"])

    @override_settings(WHATSAPP_READY=False)
    def test_without_the_number_connected_nothing_is_sent(self):
        job = self.new_job()
        self.assertEqual(job["thanks"], {"sent": False, "reason": "WhatsApp sending is not set up yet."})
        self.assertEqual(self.press(self.owner, job["number"]).status_code, 403)
        self.assertFalse(self.client_for(self.owner).get("/api/v1/state/").json()["data"]["whatsapp"]["ready"])


class ThankYouTests(WhatsAppTestCase):
    def test_goes_by_itself_when_the_card_is_saved(self):
        self.approve("thanks")
        job = self.new_job(self.aman)
        self.assertEqual(job["thanks"], {"sent": True, "reason": ""})
        sent = self.gupshup.sent[0]
        self.assertEqual((sent["to"], sent["template"]), ("919829012345", "woodstar_thanks_en_v1"))
        self.assertEqual(sent["params"][:3], ["Rajesh Meena", "WoodStar Ply Lam", f"WS-{job['number']}"])
        self.assertEqual(job["log"][-1]["txt"], "The dashboard sent the thank-you message on WhatsApp")

    def test_the_card_is_saved_even_when_the_message_cannot_go(self):
        self.approve("thanks")
        self.gupshup.fail = "provider down"
        job = self.new_job()
        self.assertFalse(job["thanks"]["sent"])
        self.assertIn("provider down", job["thanks"]["reason"])
        self.assertTrue(Job.objects.filter(number=job["number"]).exists())

    def test_can_be_sent_again_from_the_card(self):
        self.approve("thanks")
        number = self.new_job()["number"]
        self.press(self.owner, number, "thanks")
        self.assertEqual([m["template"] for m in self.gupshup.sent], ["woodstar_thanks_en_v1"] * 2)


class CustomerWritesInTests(WhatsAppTestCase):
    def test_owner_switches_replies_on(self):
        self.assertEqual(self.client_for(self.aman).post("/api/v1/whatsapp/replies/").status_code, 403)
        res = self.client_for(self.owner).post("/api/v1/whatsapp/replies/")
        self.assertTrue(res.json()["data"]["whatsapp"]["replies_on"])
        self.assertEqual(self.gupshup.subscriptions, [
            {"key": "one-time-key", "url": "https://dashboard.example.in/webhooks/whatsapp/", "tag": "woodstar-dashboard"}])

    def test_a_delivery_without_the_secret_is_refused(self):
        self.new_job()
        self.assertEqual(self.deliver("919829012345", secret=None).status_code, 403)
        self.assertEqual(self.deliver("919829012345", secret="guess").status_code, 403)
        self.assertEqual(self.gupshup.sent, [])
        self.assertEqual(self.client.get("/webhooks/whatsapp/").status_code, 200)

    def test_a_customer_is_told_where_their_order_is(self):
        job = self.new_job()
        self.tick(self.ramesh, job["number"], "ordered")
        self.assertEqual(self.deliver("919829012345", "where is my order").status_code, 200)
        reply = self.gupshup.sent[-1]
        self.assertEqual(reply["to"], "919829012345")
        self.assertIn("Namaste Rajesh Meena", reply["text"])
        self.assertIn(f"*WS-{job['number']}* — Material ordered ✓", reply["text"])
        self.assertIn("Next: Material received · promised by Tue, 20 Oct", reply["text"])
        self.assertIn(f"https://dashboard.example.in{job['track']}", reply["text"])
        self.assertNotIn("118", reply["text"])                  # never the money

    def test_a_repeated_delivery_and_a_burst_get_one_reply(self):
        self.new_job()
        self.deliver("919829012345", message_id="wamid.a")
        self.deliver("919829012345", message_id="wamid.a")      # Gupshup retrying the same message
        self.deliver("919829012345", message_id="wamid.b")      # the customer typing a second line
        self.assertEqual(len(self.gupshup.sent), 1)
        IncomingMessage.objects.update(received_at=timezone.now() - timedelta(minutes=5))
        self.deliver("919829012345", message_id="wamid.c")
        self.assertEqual(len(self.gupshup.sent), 2)

    def test_an_unknown_number_is_told_once(self):
        self.deliver("919000000001", message_id="wamid.x")
        self.assertIn("could not find an order on this number", self.gupshup.sent[0]["text"])
        IncomingMessage.objects.update(received_at=timezone.now() - timedelta(minutes=5))
        self.deliver("919000000001", message_id="wamid.y")
        self.assertEqual(len(self.gupshup.sent), 1)

    def test_hindi_customer_gets_a_hindi_reply_and_old_deliveries_drop_off(self):
        number = self.new_job(lang="hi")["number"]
        self.deliver("919829012345", message_id="wamid.h")
        self.assertIn("अगला कदम: माल ऑर्डर कर दिया गया", self.gupshup.sent[-1]["text"])
        for stage in ("ordered", "arrived", "dispatch", "delivered"):
            self.tick(self.owner, number, stage)
        Job.objects.get(number=number).ticks.filter(stage="delivered").update(done_at=timezone.now() - timedelta(days=20))
        IncomingMessage.objects.update(received_at=timezone.now() - timedelta(days=1))
        self.deliver("919829012345", message_id="wamid.late")
        self.assertIn("could not find an order", self.gupshup.sent[-1]["text"])

    def test_other_numbers_and_malformed_deliveries_are_accepted_and_ignored(self):
        self.new_job()
        self.assertEqual(self.deliver("919829012345", app="someone-elses-app").status_code, 200)
        res = self.client.post("/webhooks/whatsapp/", "not json", content_type="application/json",
                               HTTP_X_GUPSHUP_WEBHOOK_SECRET=SECRET)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.gupshup.sent, [])


@override_settings(ORDER_STATUS_SECRET="shared-secret")
class AssistantAsksTests(WhatsAppTestCase):
    """The assistant that answers the shop's number asks this dashboard for an order's status."""

    def ask(self, phone, secret="shared-secret"):
        headers = {"HTTP_X_ORDER_STATUS_SECRET": secret} if secret else {}
        return self.client.post("/hooks/order-status/", json.dumps({"phone": phone}), content_type="application/json", **headers)

    def test_it_is_given_the_words_to_send(self):
        job = self.new_job()
        self.tick(self.ramesh, job["number"], "ordered")
        res = self.ask("919829012345")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["found"])
        self.assertIn(f"*WS-{job['number']}* — Material ordered ✓", res.json()["text"])
        self.assertIn(job["track"], res.json()["text"])
        self.assertNotIn("118", res.json()["text"])
        self.assertEqual(self.gupshup.sent, [])                 # the assistant sends it; the dashboard sends nothing

    def test_a_number_with_no_order_is_left_to_the_assistant(self):
        self.assertEqual(self.ask("919000000001").json(), {"found": False})

    def test_only_a_caller_with_the_secret_is_answered(self):
        self.new_job()
        self.assertEqual(self.ask("919829012345", secret=None).status_code, 403)
        self.assertEqual(self.ask("919829012345", secret="guess").status_code, 403)
        self.assertEqual(self.client.get("/hooks/order-status/").status_code, 405)
        self.assertEqual(self.ask("12").status_code, 400)
        with override_settings(ORDER_STATUS_SECRET=""):
            self.assertEqual(self.ask("919829012345", secret="").status_code, 403)

    def test_the_dashboard_does_not_also_answer_the_number_itself(self):
        res = self.client_for(self.owner).post("/api/v1/whatsapp/replies/")
        self.assertEqual(res.status_code, 403)
        self.assertIn("already answered by the assistant", res.json()["message"])
        self.assertEqual(self.gupshup.subscriptions, [])
        self.assertTrue(self.client_for(self.owner).get("/api/v1/state/").json()["data"]["whatsapp"]["via_assistant"])


class RenamedBlanksTests(WhatsAppTestCase):
    """{job} became {order}: wording saved, and templates submitted, before the change still work."""

    def test_saved_wording_and_submitted_templates_follow_the_rename(self):
        import importlib

        from django.apps import apps

        from jobs.models import MessageWording

        MessageWording.objects.create(key="update", lang="en", body="Namaste {name}, order {job} is now {status}. See {link} soon.")
        MessageWording.objects.create(key="work", lang="en", body="Namaste {name}: {duty}. Waiting: {jobs}. Thank you.")
        row = WhatsAppTemplate.objects.create(key="update", lang="en", version=1, element_name="woodstar_update_en_v1",
                                              content="Namaste {{1}}, order {{2}} is now {{3}}.", params="name,job,status",
                                              status=WhatsAppTemplate.APPROVED)
        importlib.import_module("jobs.migrations.0005_order_blanks").forwards(apps, None)
        self.assertEqual(MessageWording.objects.get(key="update").body, "Namaste {name}, order {order} is now {status}. See {link} soon.")
        self.assertEqual(MessageWording.objects.get(key="work").body, "Namaste {name}: {duty}. Waiting: {orders}. Thank you.")
        row.refresh_from_db()
        self.assertEqual(row.params, "name,order,status")
        # and the approved template still sends, its numbers filled from the renamed blanks
        number = self.new_job()["number"]
        self.assertEqual(self.press(self.owner, number).status_code, 200)
        self.assertEqual(self.gupshup.sent[-1]["params"], ["Rajesh Meena", f"WS-{number}", "Order received"])
