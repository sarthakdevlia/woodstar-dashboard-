"""The five steps every order goes through, in order, and the two kinds of account.
Single source for the server and, through the page's bootstrap data, for the browser."""

# `said` is the line the customer reads on their tracking page; `duty` is what the worker
# doing that step is told in their daily message; `labelHi` is the step as a Hindi-reading
# customer is told it on WhatsApp.
STAGES = [
    {"key": "received", "label": "Order received", "said": "We have received your order",
     "duty": "Take orders at the counter", "dutyHi": "काउंटर पर ऑर्डर लेना", "labelHi": "ऑर्डर मिल गया"},
    {"key": "ordered", "label": "Material ordered", "said": "Your material has been ordered",
     "duty": "Order material from suppliers", "dutyHi": "सप्लायर से माल ऑर्डर करना", "labelHi": "माल ऑर्डर कर दिया गया"},
    {"key": "arrived", "label": "Material received", "said": "Your material has reached our godown",
     "duty": "Receive and check incoming material", "dutyHi": "आया हुआ माल लेना और जाँचना", "labelHi": "माल गोदाम में आ गया"},
    {"key": "dispatch", "label": "Dispatch", "said": "Your order has left our godown",
     "duty": "Load and dispatch orders", "dutyHi": "ऑर्डर लोड करके भेजना", "labelHi": "ऑर्डर रवाना हो गया"},
    {"key": "delivered", "label": "Delivered", "said": "Your order has been delivered",
     "duty": "Deliver and get it signed", "dutyHi": "डिलीवरी देना और साइन लेना", "labelHi": "डिलीवर हो गया"},
]
STAGE_KEYS = [s["key"] for s in STAGES]
STAGE_CHOICES = [(s["key"], s["label"]) for s in STAGES]
STAGE_LABELS = dict(STAGE_CHOICES)

# A worker has no fixed step: the owner gives each one their duty for the day.
OWNER, WORKER = "owner", "worker"
ROLE_CHOICES = [(OWNER, "Owner"), (WORKER, "Worker")]
ROLE_TITLES = dict(ROLE_CHOICES)

LANG_CHOICES = [("en", "English"), ("hi", "Hindi")]
