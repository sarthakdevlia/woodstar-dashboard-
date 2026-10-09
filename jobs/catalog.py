"""What the shop sells and how its messages read. Edit these lists to match the shop;
the browser receives them with the page, so nothing is duplicated there."""

# `fields` are the details asked for one item of that category, in order.
CATEGORIES = [
    {"key": "laminate", "label": "Laminate", "fields": ["brand", "thickness", "qty"], "unit": "sheets",
     "brand": ["Greenlam", "Century Laminates", "Hilaxe", "Emporio", "Eight Four", "Green Touch", "Glorio"],
     "thickness": ["0.8 mm", "1.0 mm", "1.25 mm"]},
    {"key": "adhesive", "label": "Adhesive", "fields": ["brand", "pack", "qty"], "unit": "packs",
     "brand": ["Fevicol SR", "Fevicol HeatX", "Jivanjor", "Other"],
     "pack": ["1 kg", "5 kg", "10 kg", "20 kg", "50 kg"]},
    {"key": "ply", "label": "Plywood", "fields": ["brand", "thickness", "size", "qty"], "unit": "sheets",
     "brand": ["Austin Marine", "Murphy", "Excelent", "Greenply", "Duroply", "Greenpanel"],
     "thickness": ["4 mm", "6 mm", "9 mm", "12 mm", "16 mm", "19 mm", "25 mm"],
     "size": ["8 × 4 ft", "7 × 4 ft", "8 × 3 ft", "7 × 3 ft", "6 × 4 ft", "6 × 3 ft"]},
    {"key": "louver", "label": "Louvers", "fields": ["brand", "size", "qty"], "unit": "pcs",
     "brand": ["Zurich", "Other"],
     "size": ["9.5 ft × 5 in", "9.5 ft × 6 in", "10 ft × 5 in", "10 ft × 6 in"]},
    {"key": "veneer", "label": "Veneers", "fields": ["thickness", "size", "qty"], "unit": "sheets",
     "thickness": ["0.5 mm", "3.5 mm", "4 mm"], "size": ["8 × 4 ft", "7 × 4 ft", "8 × 3 ft"]},
    {"key": "door", "label": "Doors", "fields": ["thickness", "size", "qty"], "unit": "pcs",
     "thickness": ["30 mm", "32 mm", "35 mm"],
     "size": ["7 × 3 ft", "7 × 2.5 ft", "6.5 × 3 ft", "6.5 × 2.5 ft"]},
    # Anything the shop sells outside the lists above, named by hand. `labels` renames the
    # two details it borrows, on the form only.
    {"key": "other", "label": "Other", "fields": ["brand", "size", "qty"], "unit": "pcs",
     "labels": {"brand": "Item", "size": "Size / details"}, "brand": [], "size": []},
]
CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
CATEGORY_KEYS = list(CATEGORY_BY_KEY)

SHOP = {
    "name": "WoodStar Ply Lam",
    "phone": "+91 99833 86101",
    "whatsapp": "919983386101",
    "address": "90-A, New Grain Mandi, Furniture Industrial Area, Main Road, Kota",
}

# WhatsApp wording. `blanks` are the {names} a wording may use; `required` must appear.
# "work" only fills the WhatsApp link on Today's team. "thanks" and "update" are sent from the
# shop's own number, to people who have not written first, so WhatsApp must approve each as a
# template (jobs/whatsapp.py); services.check_wording applies WhatsApp's rules before that.
TEMPLATE_DEFAULTS = {
    "work": {
        "title": "Worker's daily work", "to": "Each worker, every morning",
        "blanks": ["name", "date", "duty", "orders", "shop"], "required": ["duty", "orders"],
        "en": "Namaste {name}, your work at {shop} for {date}: {duty}. Orders waiting for you: {orders}. "
              "Tick each one on the dashboard when it is done.",
        "hi": "नमस्ते {name}, {date} को {shop} में आपका काम: {duty}। आपके ऑर्डर: {orders}। "
              "काम पूरा होने पर डैशबोर्ड पर टिक करें।",
    },
    "thanks": {
        "title": "Thank-you when the order is taken", "to": "The customer, by itself, the moment the order is saved",
        "blanks": ["name", "order", "link", "shop"], "required": ["order"],
        "en": "Namaste {name}, thank you for your order at {shop}. Your order number is {order}. "
              "Track your order any time: {link} You can also message us on this number to know where it has reached.",
        "hi": "नमस्ते {name}, {shop} से ऑर्डर करने के लिए धन्यवाद। आपका ऑर्डर नंबर {order} है। "
              "अपना ऑर्डर यहाँ देखें: {link} ऑर्डर कहाँ तक पहुँचा, यह जानने के लिए इसी नंबर पर संदेश भेजें।",
    },
    "update": {
        "title": "Order update to the customer", "to": "The customer, when someone presses the WhatsApp button on the order",
        "blanks": ["name", "order", "status", "link", "shop"], "required": ["order", "status"],
        "en": "Namaste {name}, your order {order} at {shop} is now: {status}. Track it any time: {link} Thank you.",
        "hi": "नमस्ते {name}, {shop} में आपका ऑर्डर {order} अब इस स्थिति में है: {status}। यहाँ देखें: {link} धन्यवाद।",
    },
}


def item_line(item):
    """One item as a customer reads it: 'Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets'."""
    category = CATEGORY_BY_KEY[item.category]
    details = [getattr(item, f) for f in category["fields"] if f != "qty"]
    named = "" if category["key"] == "other" else f"{category['label']}: "      # an "other" item is named by its own first detail
    return f"{named}{' · '.join(d for d in details if d)} × {item.qty} {category['unit']}"
