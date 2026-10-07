from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from jobs import services
from jobs.models import AuditEntry, Duty, Job, StageTick
from staff.models import User

PASSWORD = "shop-floor-2026"
NEW_JOB = {
    "customer_name": "Rajesh Meena", "customer_phone": "9829012345", "site": "Kunhadi", "mode": "Delivery",
    "due": "2026-10-20", "amount": 118400, "advance": 50000,
    "items": [{"category": "ply", "brand": "Austin Marine", "thickness": "19 mm", "size": "8 × 4 ft", "pack": "ignored", "qty": 24},
              {"category": "adhesive", "brand": "Fevicol SR", "pack": "20 kg", "qty": 2}],
}


def make_user(username, role="worker", **extra):
    user = User(username=username, name=username.title(), role=role, **extra)
    user.set_password(PASSWORD)
    user.save()
    return user


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("piyush", "owner")
        self.aman = make_user("aman", phone="9829011111", lang="en")
        self.ramesh = make_user("ramesh", phone="9829022222")
        self.salim = make_user("salim")
        for worker, stage in ((self.aman, "received"), (self.ramesh, "ordered"), (self.salim, "arrived")):
            services.set_duty(self.owner, worker.id, stage, True)

    def client_for(self, user):
        client = APIClient()
        client.force_login(user)
        return client

    def new_job(self, user=None, **changes):
        res = self.client_for(user or self.owner).post("/api/v1/jobs/", {**NEW_JOB, **changes}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["data"]

    def tick(self, user, number, stage):
        return self.client_for(user).put(f"/api/v1/jobs/{number}/stages/{stage}/")


class AuthTests(ApiTestCase):
    def test_api_requires_sign_in(self):
        res = APIClient().get("/api/v1/state/")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.json()["success"])

    def test_dashboard_page_redirects_to_login(self):
        self.assertRedirects(self.client.get("/"), "/login/?next=/")

    def test_sign_in_with_the_login_page(self):
        res = self.client.post("/login/", {"username": "aman", "password": PASSWORD})
        self.assertRedirects(res, "/")
        self.assertContains(self.client.get("/"), "Signed in as <b>Aman</b>")

    def test_five_wrong_passwords_lock_that_username_out(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/login/", {"username": "aman", "password": "wrong"}).status_code, 200)
        res = self.client.post("/login/", {"username": "aman", "password": PASSWORD})
        self.assertEqual(res.status_code, 429)
        self.assertContains(res, "Too many failed attempts", status_code=429)

    def test_switched_off_account_loses_access_immediately(self):
        client = self.client_for(self.aman)
        self.aman.is_active = False
        self.aman.save()
        self.assertEqual(client.get("/api/v1/state/").status_code, 403)


class JobCardTests(ApiTestCase):
    def test_owner_creates_a_card_and_it_starts_at_order_received(self):
        job = self.new_job()
        self.assertEqual(job["no"], "WS-1001")
        self.assertEqual(job["stages"]["received"]["by"], "Piyush")
        self.assertIsNone(job["stages"]["ordered"])
        self.assertEqual(job["amount"], 118400)
        # only the details that category asks for are kept
        self.assertEqual(job["items"][0], {"cat": "ply", "qty": 24, "brand": "Austin Marine", "thickness": "19 mm", "size": "8 × 4 ft"})
        self.assertEqual(self.new_job()["no"], "WS-1002")

    def test_counter_worker_creates_cards_but_cannot_set_money(self):
        job = self.new_job(self.aman)
        self.assertNotIn("amount", job)
        self.assertEqual(Job.objects.get(number=job["number"]).amount, 0)

    def test_worker_without_counter_duty_cannot_create(self):
        res = self.client_for(self.ramesh).post("/api/v1/jobs/", NEW_JOB, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertIn("Order received", res.json()["message"])

    def test_bad_input_is_refused_with_field_errors(self):
        res = self.client_for(self.owner).post("/api/v1/jobs/", {**NEW_JOB, "customer_phone": "12345", "items": []}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.json()["errors"]), {"customer_phone", "items"})

    def test_workers_never_see_money(self):
        number = self.new_job()["number"]
        for path in ("/api/v1/state/", f"/api/v1/jobs/{number}/"):
            body = self.client_for(self.ramesh).get(path).content.decode()
            self.assertNotIn("amount", body)
            self.assertNotIn("118400", body)

    def test_only_owner_edits_payment_and_it_is_audited(self):
        number = self.new_job()["number"]
        self.assertEqual(self.client_for(self.ramesh).patch(f"/api/v1/jobs/{number}/", {"advance": 1}, format="json").status_code, 403)
        res = self.client_for(self.owner).patch(f"/api/v1/jobs/{number}/", {"advance": 118400}, format="json")
        self.assertEqual(res.json()["data"]["advance"], 118400)
        self.assertEqual(res.json()["data"]["log"][-1]["txt"], "Piyush edited advance")

    def test_delivered_cards_leave_the_board_after_two_weeks(self):
        number = self.new_job()["number"]
        for stage in ("ordered", "arrived", "dispatch", "delivered"):
            self.assertEqual(self.tick(self.owner, number, stage).status_code, 200)
        StageTick.objects.filter(stage="delivered").update(done_at=services.timezone.now() - timedelta(days=15))
        client = self.client_for(self.owner)
        self.assertEqual(client.get("/api/v1/state/").json()["data"]["jobs"], [])
        self.assertEqual(len(client.get("/api/v1/state/?scope=all").json()["data"]["jobs"]), 1)


class StepTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.number = self.new_job()["number"]

    def test_worker_ticks_their_duty_in_order(self):
        res = self.tick(self.ramesh, self.number, "ordered")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["stages"]["ordered"]["by"], "Ramesh")
        self.assertEqual(self.tick(self.salim, self.number, "arrived").status_code, 200)
        self.assertEqual(AuditEntry.objects.filter(action=AuditEntry.TICKED).count(), 2)

    def test_worker_cannot_tick_someone_elses_duty(self):
        res = self.tick(self.aman, self.number, "ordered")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["message"], '"Material ordered" is Ramesh\'s duty today, not yours.')

    def test_steps_cannot_be_skipped(self):
        res = self.tick(self.salim, self.number, "arrived")
        self.assertEqual(res.status_code, 403)
        self.assertIn('"Material ordered" has to be done first', res.json()["message"])

    def test_only_owner_undoes_and_only_the_latest_step(self):
        self.tick(self.ramesh, self.number, "ordered")
        self.tick(self.salim, self.number, "arrived")
        self.assertEqual(self.client_for(self.ramesh).delete(f"/api/v1/jobs/{self.number}/stages/ordered/").status_code, 403)
        owner = self.client_for(self.owner)
        self.assertIn("later steps", owner.delete(f"/api/v1/jobs/{self.number}/stages/ordered/").json()["message"])
        res = owner.delete(f"/api/v1/jobs/{self.number}/stages/arrived/")
        self.assertIsNone(res.json()["data"]["stages"]["arrived"])
        self.assertEqual(res.json()["data"]["log"][-1]["txt"], "Piyush undid Material received")

    def test_a_duty_taken_away_stops_the_ticking(self):
        self.client_for(self.owner).put("/api/v1/roster/", {"user_id": self.ramesh.id, "stage": "ordered", "on": False}, format="json")
        self.assertEqual(self.tick(self.ramesh, self.number, "ordered").status_code, 403)

    def test_unknown_step_and_unknown_job(self):
        self.assertEqual(self.tick(self.owner, self.number, "painted").status_code, 404)
        self.assertEqual(self.tick(self.owner, 99999, "ordered").status_code, 404)


class RosterTests(ApiTestCase):
    def test_state_shows_todays_duties(self):
        data = self.client_for(self.ramesh).get("/api/v1/state/").json()["data"]
        self.assertEqual(data["me"]["duties"], ["ordered"])
        self.assertEqual(data["roster"][str(self.aman.id)], ["received"])
        self.assertEqual([w["name"] for w in data["workers"]], ["Aman", "Ramesh", "Salim"])

    def test_only_owner_sets_duties(self):
        body = {"user_id": self.salim.id, "stage": "dispatch", "on": True}
        self.assertEqual(self.client_for(self.salim).put("/api/v1/roster/", body, format="json").status_code, 403)
        res = self.client_for(self.owner).put("/api/v1/roster/", body, format="json")
        self.assertEqual(res.json()["data"]["roster"][str(self.salim.id)], ["arrived", "dispatch"])

    def test_tomorrow_starts_with_todays_duties_then_changes_on_its_own(self):
        tomorrow = services.today() + timedelta(days=1)
        with mock.patch("jobs.services.today", return_value=tomorrow):
            self.assertEqual(services.roster_for(tomorrow)[self.ramesh.id], ["ordered"])
            services.set_duty(self.owner, self.ramesh.id, "ordered", False)
            self.assertNotIn(self.ramesh.id, services.roster_for(tomorrow))
        self.assertEqual(services.roster_for(services.today())[self.ramesh.id], ["ordered"])
        self.assertEqual(Duty.objects.filter(day=tomorrow).count(), 2)

    def test_marking_work_sent_and_a_changed_duty_clears_it(self):
        owner = self.client_for(self.owner)
        res = owner.post("/api/v1/roster/sent/", {"user_ids": [self.aman.id, self.ramesh.id]}, format="json")
        self.assertEqual(set(res.json()["data"]["sent"]), {str(self.aman.id), str(self.ramesh.id)})
        res = owner.put("/api/v1/roster/", {"user_id": self.aman.id, "stage": "dispatch", "on": True}, format="json")
        self.assertEqual(set(res.json()["data"]["sent"]), {str(self.ramesh.id)})


class StaffTests(ApiTestCase):
    def test_only_owner_manages_staff(self):
        self.assertEqual(self.client_for(self.aman).get("/api/v1/staff/").status_code, 403)
        self.assertEqual(self.client_for(self.aman).patch(f"/api/v1/staff/{self.aman.id}/", {"role": "owner"}, format="json").status_code, 403)

    def test_owner_adds_a_worker_who_can_then_sign_in(self):
        body = {"username": "vijay", "name": "Vijay", "role": "worker", "phone": "9829044444", "lang": "hi", "password": PASSWORD}
        res = self.client_for(self.owner).post("/api/v1/staff/", body, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("password", res.json()["data"])
        self.assertTrue(self.client.login(username="vijay", password=PASSWORD))
        self.assertEqual(self.client_for(self.owner).post("/api/v1/staff/", body, format="json").json()["message"], "That username is already taken.")

    def test_weak_password_is_refused(self):
        body = {"username": "vijay", "name": "Vijay", "role": "worker", "password": "12345678"}
        self.assertEqual(self.client_for(self.owner).post("/api/v1/staff/", body, format="json").status_code, 403)

    def test_role_menu_and_switching_off(self):
        owner = self.client_for(self.owner)
        self.assertEqual(owner.patch(f"/api/v1/staff/{self.aman.id}/", {"role": "owner"}, format="json").json()["data"]["title"], "Owner")
        owner.patch(f"/api/v1/staff/{self.ramesh.id}/", {"is_active": False}, format="json")
        data = owner.get("/api/v1/state/").json()["data"]
        self.assertEqual([w["name"] for w in data["workers"]], ["Salim"])      # an owner and a switched-off worker are not on the team

    def test_there_is_always_an_owner(self):
        res = self.client_for(self.owner).patch(f"/api/v1/staff/{self.owner.id}/", {"role": "worker"}, format="json")
        self.assertEqual(res.json()["message"], "There must always be at least one active owner.")


class WordingTests(ApiTestCase):
    def test_owner_saves_and_resets_wording(self):
        owner = self.client_for(self.owner)
        text = "Namaste {name}, order {job} is now {status}. See {link} for details."
        res = owner.put("/api/v1/templates/update/en/", {"body": text}, format="json")
        self.assertEqual(res.json()["data"]["update"]["en"], text)
        self.assertEqual(owner.get("/api/v1/state/").json()["data"]["templates"]["update"]["en"], text)
        res = owner.delete("/api/v1/templates/update/en/")
        self.assertIn("Track it any time", res.json()["data"]["update"]["en"])

    def test_wording_whatsapp_would_refuse_is_not_saved(self):
        owner = self.client_for(self.owner)
        for text, why in (("{name}, order {job} is now {status}, thank you very much.", "begin or end"),
                          ("Namaste, your order {job} has moved on, thank you.", "{status}"),
                          ("Namaste {customer}, order {job} is now {status}, thank you.", "{customer}"),
                          ("Order {job} is {status} ok.", "Too short")):
            res = owner.put("/api/v1/templates/update/en/", {"body": text}, format="json")
            self.assertEqual(res.status_code, 403, text)
            self.assertIn(why, res.json()["message"])

    def test_workers_cannot_change_wording(self):
        res = self.client_for(self.aman).put("/api/v1/templates/work/hi/", {"body": "x" * 40}, format="json")
        self.assertEqual(res.status_code, 403)


class TrackingTests(ApiTestCase):
    def test_customer_link_needs_no_login_and_follows_the_ticks(self):
        job = self.new_job()
        res = self.client.get(job["track"])
        self.assertContains(res, "Job card WS-1001")
        self.assertContains(res, "Next: Material ordered")
        self.assertContains(res, "Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets")
        self.assertNotContains(res, "118")                # no money on the customer's page
        self.assertNotContains(res, "9829012345")
        self.tick(self.ramesh, job["number"], "ordered")
        self.assertContains(self.client.get(job["track"]), "Next: Material received")

    def test_a_guessed_link_finds_nothing(self):
        self.new_job()
        self.assertEqual(self.client.get("/t/WS-1001/").status_code, 404)
        self.assertEqual(self.client.get("/t/aaaaaaaaaaaaaaaa/").status_code, 404)

    def test_health_check(self):
        self.assertEqual(self.client.get("/healthz").content, b"ok")
