import re

from django.db import migrations

# "Job card" became "order" on every screen, and with it the blanks a message wording may
# use: {job} is now {order}, {jobs} is now {orders}. Wording the shop has already saved, and
# the record of which blank each number of a submitted template stands for, follow suit.
# A template's own text is untouched: WhatsApp holds it with numbers, not names.
RENAMED = {"job": "order", "jobs": "orders"}


def rename(apps, mapping):
    wording = apps.get_model("jobs", "MessageWording")
    for row in wording.objects.all():
        body = re.sub(r"\{(%s)\}" % "|".join(mapping), lambda m: "{%s}" % mapping[m.group(1)], row.body)
        if body != row.body:
            row.body = body
            row.save(update_fields=["body"])
    template = apps.get_model("jobs", "WhatsAppTemplate")
    for row in template.objects.all():
        params = ",".join(mapping.get(name, name) for name in row.params.split(","))
        if params != row.params:
            row.params = params
            row.save(update_fields=["params"])


def forwards(apps, schema_editor):
    rename(apps, RENAMED)


def backwards(apps, schema_editor):
    rename(apps, {new: old for old, new in RENAMED.items()})


class Migration(migrations.Migration):
    dependencies = [("jobs", "0004_other_item")]
    operations = [migrations.RunPython(forwards, backwards)]
