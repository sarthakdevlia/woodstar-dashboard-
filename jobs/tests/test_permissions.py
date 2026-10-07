from django.test import SimpleTestCase

from jobs.permissions import check_create, check_tick


class CheckTickTests(SimpleTestCase):
    def test_owner_ticks_any_step_in_order(self):
        self.assertIsNone(check_tick(True, [], "ordered", {"received"}))
        self.assertIn('"Order received" has to be done first', check_tick(True, [], "ordered", set()))

    def test_worker_cannot_tick_a_step_that_is_not_their_duty(self):
        reason = check_tick(False, ["ordered"], "arrived", {"received", "ordered"}, on_duty=["Salim"])
        self.assertEqual(reason, '"Material received" is Salim\'s duty today, not yours.')
        self.assertIn("nobody's duty", check_tick(False, [], "arrived", {"received", "ordered"}))

    def test_worker_must_follow_the_order(self):
        self.assertIn('"Material ordered" has to be done first', check_tick(False, ["arrived"], "arrived", {"received"}))
        self.assertIsNone(check_tick(False, ["arrived"], "arrived", {"received", "ordered"}))

    def test_worker_cannot_undo(self):
        self.assertEqual(check_tick(False, ["ordered"], "ordered", {"received", "ordered"}),
                         "Only the owner can undo a completed step.")

    def test_owner_undoes_only_the_latest_step(self):
        done = {"received", "ordered", "arrived"}
        self.assertIsNone(check_tick(True, [], "arrived", done))
        self.assertEqual(check_tick(True, [], "ordered", done), "Undo the later steps first.")


class CheckCreateTests(SimpleTestCase):
    def test_counter_duty_or_owner_creates_job_cards(self):
        self.assertIsNone(check_create(True, []))
        self.assertIsNone(check_create(False, ["received"]))
        self.assertIn("Order received", check_create(False, ["dispatch"]))
