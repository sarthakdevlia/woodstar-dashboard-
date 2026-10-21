from jobs.models import AuditEntry, JobItem, Purchase, StageTick
from jobs.tests.test_api import NEW_JOB, ApiTestCase, make_user


class MaterialTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.packer = make_user("packer")  # on Dispatch only: no material
        from jobs import services
        services.set_duty(self.owner, self.packer.id, "dispatch", True)

    def items(self, job):
        return {i["cat"]: i for i in job["items"]}

    def add_supplier(self, user=None, **body):
        res = self.client_for(user or self.ramesh).post("/api/v1/suppliers/", {"name": "Sharma Traders", "phone": "9829033333", **body}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["data"]["suppliers"][0]

    def place(self, user, supplier_id, item_ids, **extra):
        return self.client_for(user).post("/api/v1/purchases/", {"supplier_id": supplier_id, "item_ids": item_ids, **extra}, format="json")

    def test_items_start_not_checked_unless_the_counter_says(self):
        job = self.new_job(self.aman, items=[{**NEW_JOB["items"][0], "source": "supplier"}, {**NEW_JOB["items"][1], "source": "stock"},
                                             {"category": "laminate", "brand": "Greenlam", "thickness": "1.0 mm", "qty": 4}])
        self.assertEqual([i["source"] for i in job["items"]], ["supplier", "stock", ""])
        self.assertIsNone(job["items"][0]["purchase"])

    def test_marking_an_item_is_for_material_duties_and_is_audited(self):
        job = self.new_job()
        ply = self.items(job)["ply"]
        res = self.client_for(self.packer).put(f"/api/v1/items/{ply['id']}/source/", {"source": "stock"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertIn("Material is handled by", res.json()["message"])
        res = self.client_for(self.salim).put(f"/api/v1/items/{ply['id']}/source/", {"source": "stock"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.items(res.json()["data"])["ply"]["source"], "stock")
        entry = AuditEntry.objects.filter(action=AuditEntry.MATERIAL).get()
        self.assertEqual((entry.actor_name, entry.detail), ("Salim", "marked Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets as in stock"))

    def test_a_purchase_is_placed_received_and_never_ticks_the_order(self):
        job = self.new_job()
        supplier = self.add_supplier()
        ply, glue = self.items(job)["ply"], self.items(job)["adhesive"]
        res = self.place(self.ramesh, supplier["id"], [ply["id"], glue["id"]], expected="2026-10-25", note="To the godown")
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()["data"]
        po = next(p for p in data["purchases"] if p["id"] == data["placed"])
        self.assertEqual((po["code"], po["status"], po["expected"], po["supplier"]["name"]), (f"PO-{po['id']}", "ordered", "2026-10-25", "Sharma Traders"))
        self.assertEqual([i["job"] for i in po["items"]], [job["no"]] * 2)
        self.assertEqual(po["items"][0]["line"], "Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets")
        self.assertEqual(JobItem.objects.get(pk=glue["id"]).source, JobItem.SUPPLIER)

        detail = self.client_for(self.owner).get(f"/api/v1/jobs/{job['number']}/").json()["data"]
        self.assertEqual(self.items(detail)["ply"]["purchase"]["status"], "ordered")
        self.assertIn("ordered Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets from Sharma Traders", detail["log"][-2]["txt"])

        # the same item cannot go on a second purchase
        self.assertEqual(self.place(self.ramesh, supplier["id"], [ply["id"]]).status_code, 403)

        res = self.client_for(self.salim).post(f"/api/v1/purchases/{po['id']}/received/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Purchase.objects.get().received_by_name, "Salim")
        # "Material ordered" / "Material received" stay the staff's own ticks
        self.assertEqual(set(StageTick.objects.filter(job__number=job["number"]).values_list("stage", flat=True)), {"received"})
        self.assertEqual(self.client_for(self.salim).post(f"/api/v1/purchases/{po['id']}/received/").status_code, 403)

    def test_cancelling_puts_the_items_back_to_order(self):
        job = self.new_job()
        supplier = self.add_supplier()
        ply = self.items(job)["ply"]
        po_id = self.place(self.owner, supplier["id"], [ply["id"]]).json()["data"]["placed"]
        # while it is on a purchase, it cannot be re-marked
        res = self.client_for(self.ramesh).put(f"/api/v1/items/{ply['id']}/source/", {"source": "stock"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client_for(self.ramesh).delete(f"/api/v1/purchases/{po_id}/").status_code, 200)
        item = JobItem.objects.get(pk=ply["id"])
        self.assertEqual((item.purchase_id, item.source), (None, JobItem.SUPPLIER))
        self.assertFalse(Purchase.objects.exists())

    def test_material_state_is_only_for_material_duties(self):
        self.add_supplier()
        self.assertTrue(self.client_for(self.aman).get("/api/v1/state/").json()["data"]["material"]["allowed"])
        hidden = self.client_for(self.packer).get("/api/v1/state/").json()["data"]["material"]
        self.assertEqual(hidden, {"allowed": False, "suppliers": [], "purchases": []})
        self.assertEqual(self.client_for(self.packer).post("/api/v1/suppliers/", {"name": "X"}, format="json").status_code, 403)

    def test_suppliers_are_unique_by_name_and_need_a_real_number(self):
        supplier = self.add_supplier()
        res = self.client_for(self.ramesh).post("/api/v1/suppliers/", {"name": "sharma traders"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client_for(self.ramesh).post("/api/v1/suppliers/", {"name": "Gupta", "phone": "12345"}, format="json").status_code, 400)
        res = self.client_for(self.ramesh).patch(f"/api/v1/suppliers/{supplier['id']}/", {"name": "Sharma & Sons", "phone": ""}, format="json")
        self.assertEqual(res.json()["data"]["suppliers"][0], {"id": supplier["id"], "name": "Sharma & Sons", "phone": ""})
