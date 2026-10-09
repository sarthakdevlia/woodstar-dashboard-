import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs import services
from jobs.models import AuditEntry, Job, JobItem, StageTick
from jobs.stages import OWNER, STAGE_KEYS, WORKER
from staff.models import User

WORKERS = [  # username, name, phone, language, duty
    ("aman", "Aman", "9829011111", "en", ["received"]),
    ("ramesh", "Ramesh", "9829022222", "hi", ["ordered"]),
    ("salim", "Salim", "9829033333", "hi", ["arrived"]),
    ("vijay", "Vijay", "9829044444", "hi", ["dispatch", "delivered"]),
]
JOBS = [  # customer, phone, site, carpenter, mode, due in days, items, value, advance, hours since each step
    ("Rajesh Meena", "9829012345", "Kunhadi — new house", "Mohan (carpenter)", "Delivery", 1,
     [("ply", {"brand": "Austin Marine", "thickness": "19 mm", "size": "8 × 4 ft", "qty": 24}),
      ("laminate", {"brand": "Greenlam", "thickness": "1.0 mm", "qty": 30}),
      ("adhesive", {"brand": "Fevicol SR", "pack": "20 kg", "qty": 2})], 118400, 50000, [52, 49, 30, 6]),
    ("Sharma Interiors", "9414523456", "Talwandi — office fit-out", "", "Delivery", 2,
     [("ply", {"brand": "Murphy", "thickness": "19 mm", "size": "8 × 4 ft", "qty": 40}),
      ("louver", {"brand": "Zurich", "size": "9.5 ft × 5 in", "qty": 36})], 212000, 100000, [30, 22, 5]),
    ("Kavita Jain", "9660034567", "Vigyan Nagar — kitchen", "Anil (carpenter)", "Pickup", 0,
     [("laminate", {"brand": "Century Laminates", "thickness": "1.0 mm", "qty": 14}),
      ("ply", {"brand": "Excelent", "thickness": "12 mm", "size": "8 × 4 ft", "qty": 10})], 46200, 46200, [20, 16, 9]),
    ("Anand Builders", "9829567812", "Borkhera — 12 flats", "", "Delivery", 5,
     [("door", {"thickness": "32 mm", "size": "7 × 3 ft", "qty": 48}),
      ("ply", {"brand": "Excelent", "thickness": "19 mm", "size": "8 × 4 ft", "qty": 60})], 486000, 150000, [8, 6]),
    ("Priya Agarwal", "9887123450", "Mahaveer Nagar — wardrobe", "Mohan (carpenter)", "Pickup", 1,
     [("veneer", {"thickness": "4 mm", "size": "8 × 4 ft", "qty": 8}),
      ("laminate", {"brand": "Hilaxe", "thickness": "0.8 mm", "qty": 6})], 38900, 20000, [5, 2]),
    ("Suresh Kumawat", "9950011223", "Rangbari — TV unit", "", "Pickup", 3,
     [("louver", {"brand": "Zurich", "size": "10 ft × 6 in", "qty": 12})], 21600, 5000, [3]),
    ("Verma Furnitures", "9413345566", "Workshop order", "", "Delivery", 0,
     [("ply", {"brand": "Austin Marine", "thickness": "9 mm", "size": "8 × 4 ft", "qty": 30})], 96500, 96500, [70, 66, 50, 30, 20]),
]


class Command(BaseCommand):
    """Sample people and orders for trying the dashboard on your own computer.
    Refuses to run on a database that already has orders, so it cannot touch real data."""

    help = "Fill an empty local database with sample staff and orders. Needs DEMO_PASSWORD."

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD", "")
        if len(password) < 8:
            raise CommandError("Set DEMO_PASSWORD (8 or more characters); every sample account gets it.")
        if Job.objects.exists():
            raise CommandError("This database already has orders; not adding sample data.")

        def person(username, name, role, phone="", lang="en"):
            user, created = User.objects.get_or_create(username=username, defaults={"name": name, "role": role, "phone": phone, "lang": lang})
            if created:
                user.set_password(password)
                user.save()
            return user

        owner = person("piyush", "Piyush", OWNER)
        by_stage = {}
        for username, name, phone, lang, duty in WORKERS:
            worker = person(username, name, WORKER, phone, lang)
            for stage in duty:
                services.set_duty(owner, worker.id, stage, True)
                by_stage[stage] = worker

        now = timezone.now()
        for customer, phone, site, contractor, mode, due_in, items, amount, advance, hours in JOBS:
            created = now - timedelta(hours=hours[0])
            job = Job.objects.create(
                number=services.next_job_number(), customer_name=customer, customer_phone=phone, site=site,
                contractor=contractor, mode=mode, due=services.today() + timedelta(days=due_in), amount=amount,
                advance=advance, created_by=by_stage["received"], created_at=created, stage_changed_at=now - timedelta(hours=hours[-1]))
            for position, (category, details) in enumerate(items):
                JobItem.objects.create(job=job, category=category, position=position, **details)
            for stage, ago in zip(STAGE_KEYS, hours):
                who, at = by_stage[stage], now - timedelta(hours=ago)
                StageTick.objects.create(job=job, stage=stage, done_by=who, done_by_name=who.name, done_at=at)
                AuditEntry.objects.create(job=job, actor=who, actor_name=who.name, created_at=at, stage=stage,
                                          action=AuditEntry.CREATED if stage == STAGE_KEYS[0] else AuditEntry.TICKED)
        self.stdout.write(self.style.SUCCESS(
            f"Added {len(JOBS)} orders. Sign in as piyush (owner) or {', '.join(w[0] for w in WORKERS)} (workers)."))
