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
# The rules in services.check_wording are the ones WhatsApp applies to approved templates,
# so a wording saved here can later be submitted as one unchanged.
TEMPLATE_DEFAULTS = {
    "work": {
        "title": "Worker's daily work", "to": "Each worker, every morning",
        "blanks": ["name", "date", "duty", "jobs", "shop"], "required": ["duty", "jobs"],
        "en": "Namaste {name}, your work at {shop} for {date}: {duty}. Job cards waiting for you: {jobs}. "
              "Tick each one on the dashboard when it is done.",
        "hi": "नमस्ते {name}, {date} को {shop} में आपका काम: {duty}। आपके जॉब कार्ड: {jobs}। "
              "काम पूरा होने पर डैशबोर्ड पर टिक करें।",
    },
    "update": {
        "title": "Order update to the customer", "to": "The customer, at every step",
        "blanks": ["name", "job", "status", "link", "shop"], "required": ["job", "status"],
        "en": "Namaste {name}, your order {job} at {shop} is now: {status}. Track it any time: {link} Thank you.",
        "hi": "नमस्ते {name}, {shop} में आपका ऑर्डर {job} अब इस स्थिति में है: {status}। यहाँ देखें: {link} धन्यवाद।",
    },
}


def item_line(item):
    """One item as a customer reads it: 'Plywood: Austin Marine · 19 mm · 8 × 4 ft × 24 sheets'."""
    category = CATEGORY_BY_KEY[item.category]
    details = [getattr(item, f) for f in category["fields"] if f != "qty"]
    return f"{category['label']}: {' · '.join(d for d in details if d)} × {item.qty} {category['unit']}"
