"""Material for customer orders: which items are already in the godown, which are bought
from a supplier, and the purchases that bring them in.

An item starts "not checked". Whoever takes the order, orders from suppliers or receives
material marks it in stock or from a supplier; a purchase gathers items for one supplier,
is ordered, then received. Nothing here ticks an order's steps — "Material ordered" and
"Material received" stay the staff's own ticks.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .catalog import item_line
from .models import AuditEntry, JobItem, Purchase, Supplier
from .permissions import check_material
from .services import Denied, duties_of

SUPPLIERS_MAX = 200
PURCHASE_ITEMS_MAX = 100
# A received purchase stays on the screen this long, then only its orders remember it.
RECEIVED_VISIBLE = timedelta(days=14)
SOURCE_SAID = {JobItem.UNCHECKED: "not checked", JobItem.STOCK: "in stock", JobItem.SUPPLIER: "from a supplier"}


def allowed(user):
    return check_material(user.is_owner, duties_of(user)) is None


def _require(user):
    reason = check_material(user.is_owner, duties_of(user))
    if reason:
        raise Denied(reason)


def _audit(item, user, detail):
    AuditEntry.objects.create(job_id=item.job_id, actor=user, actor_name=user.name,
                              action=AuditEntry.MATERIAL, detail=detail[:255])


# ---------------------------------------------------------------- items

@transaction.atomic
def set_source(user, item_id, source):
    """Mark one item in stock, from a supplier, or back to not checked. Returns its order's number."""
    _require(user)
    item = JobItem.objects.select_for_update().get(pk=item_id)
    if item.source == source:
        return item.job.number
    if item.purchase_id:
        raise Denied(f"It is on {item.purchase.code} already. Cancel that purchase first.")
    item.source = source
    item.save(update_fields=["source"])
    _audit(item, user, f"marked {item_line(item)} as {SOURCE_SAID[source]}")
    return item.job.number


# ---------------------------------------------------------------- suppliers

def save_supplier(user, name, phone, supplier_id=None):
    _require(user)
    if supplier_id is None:
        if Supplier.objects.count() >= SUPPLIERS_MAX:
            raise Denied(f"The list holds {SUPPLIERS_MAX} suppliers.")
        if Supplier.objects.filter(name__iexact=name).exists():
            raise Denied(f"{name} is already on the list.")
        return Supplier.objects.create(name=name, phone=phone)
    supplier = Supplier.objects.get(pk=supplier_id)
    if Supplier.objects.filter(name__iexact=name).exclude(pk=supplier.pk).exists():
        raise Denied(f"{name} is already on the list.")
    supplier.name, supplier.phone = name, phone
    supplier.save(update_fields=["name", "phone"])
    return supplier


# ---------------------------------------------------------------- purchases

@transaction.atomic
def place(user, supplier_id, item_ids, expected=None, note=""):
    """Order these items from one supplier. Each item may be on one purchase at a time."""
    _require(user)
    supplier = Supplier.objects.get(pk=supplier_id)
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise Denied("Choose the items to order.")
    if len(ids) > PURCHASE_ITEMS_MAX:
        raise Denied(f"One purchase holds at most {PURCHASE_ITEMS_MAX} items.")
    items = list(JobItem.objects.select_for_update().filter(pk__in=ids).select_related("job"))
    if len(items) != len(ids):
        raise Denied("Some of those items are no longer there. Refresh and try again.")
    taken = [i for i in items if i.purchase_id]
    if taken:
        raise Denied(f"{item_line(taken[0])} for {taken[0].job.code} has been ordered already.")
    purchase = Purchase.objects.create(supplier=supplier, expected=expected, note=note, created_by_name=user.name)
    for item in items:
        item.source, item.purchase = JobItem.SUPPLIER, purchase
        item.save(update_fields=["source", "purchase"])
        _audit(item, user, f"ordered {item_line(item)} from {supplier.name} ({purchase.code})")
    return purchase


def _open_purchase(purchase_id):
    purchase = Purchase.objects.select_for_update().select_related("supplier").get(pk=purchase_id)
    if purchase.status != Purchase.ORDERED:
        raise Denied(f"{purchase.code} has been received already.")
    return purchase


@transaction.atomic
def receive(user, purchase_id):
    """The material is in. The orders' own "Material received" ticks stay with the staff."""
    _require(user)
    purchase = _open_purchase(purchase_id)
    purchase.status, purchase.received_at, purchase.received_by_name = Purchase.RECEIVED, timezone.now(), user.name
    purchase.save(update_fields=["status", "received_at", "received_by_name"])
    for item in purchase.items.all():
        _audit(item, user, f"received {item_line(item)} from {purchase.supplier.name} ({purchase.code})")
    return purchase


@transaction.atomic
def cancel(user, purchase_id):
    """Call off a purchase still waiting: its items go back on the to-order list."""
    _require(user)
    purchase = _open_purchase(purchase_id)
    for item in purchase.items.all():
        _audit(item, user, f"cancelled {purchase.code} for {item_line(item)}")
    purchase.items.update(purchase=None)
    purchase.delete()


# ---------------------------------------------------------------- what the screen shows

def _stamp(dt):
    return timezone.localtime(dt).isoformat() if dt else None


def supplier_to_dict(supplier):
    return {"id": supplier.id, "name": supplier.name, "phone": supplier.phone}


def purchase_to_dict(purchase):
    return {
        "id": purchase.id, "code": purchase.code, "status": purchase.status,
        "supplier": supplier_to_dict(purchase.supplier),
        "expected": purchase.expected.isoformat() if purchase.expected else None,
        "note": purchase.note,
        "created_by": purchase.created_by_name, "created_at": _stamp(purchase.created_at),
        "received_by": purchase.received_by_name, "received_at": _stamp(purchase.received_at),
        "items": [{"id": i.id, "line": item_line(i), "job": i.job.code} for i in purchase.items.all()],
    }


def state(user):
    """The Material screen: the supplier list and the purchases still worth showing."""
    if not allowed(user):
        return {"allowed": False, "suppliers": [], "purchases": []}
    recent = timezone.now() - RECEIVED_VISIBLE
    purchases = (Purchase.objects.filter(status=Purchase.ORDERED) | Purchase.objects.filter(received_at__gte=recent))
    purchases = purchases.select_related("supplier").prefetch_related("items__job").order_by("-created_at", "-id")[:200]
    return {
        "allowed": True,
        "suppliers": [supplier_to_dict(s) for s in Supplier.objects.all()[:SUPPLIERS_MAX]],
        "purchases": [purchase_to_dict(p) for p in purchases],
    }
