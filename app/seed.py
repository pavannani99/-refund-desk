"""Sample orders and buyer messages. Each order gets a real captured payment in the PayPal sandbox when
credentials are set, so approving a refund in the grid moves sandbox money."""
from datetime import date, timedelta

from . import db, paypal


def _d(days_ago: int | None) -> str | None:
    return None if days_ago is None else (date.today() - timedelta(days=days_ago)).isoformat()


# (order id, buyer, email, item, amount, ordered days ago, delivery status, delivered days ago, channel, subject, message)
SAMPLES = [
    ("ORD-1001", "Maya Thompson", "maya.t@example.com", "Wireless Lavalier Mic (2-pack)", 59.99, 9, "delivered", 4,
     "email", "Mic won't turn on",
     "Hi, the second mic in the pack won't turn on at all. I charged it overnight and the light never comes on. "
     "The first one works fine. Can you help?"),
    ("ORD-1002", "James Okafor", "j.okafor@example.com", "Podcast Starter Kit", 149.00, 24, "in_transit", None,
     "paypal_dispute", "Item not received",
     "My order still hasn't arrived. Tracking hasn't moved in 16 days. I want my money back or I'll open a "
     "chargeback with my bank."),
    ("ORD-1003", "Priya Natarajan", "priya.n@example.com", "Clip-on Mic - Black", 49.99, 12, "delivered", 7,
     "email", "Scratched case",
     "The mic works great but the charging case arrived with a deep scratch across the lid. A bit disappointing "
     "for a gift."),
    ("ORD-1004", "Lucas Martin", "lucas.martin@example.com", "USB Audio Interface", 89.00, 55, "delivered", 50,
     "email", "Return request",
     "Hey, I changed my mind about the interface, I don't really need it anymore. Can I get a refund?"),
    ("ORD-1005", "Sofia Ramirez", "sofia.r@example.com", "Clip-on Mic - White", 39.99, 10, "delivered", 5,
     "email", "Mi micrófono no funciona",
     "Hola, el micrófono no funciona, no se conecta con mi teléfono y la luz parpadea en rojo. ¿Qué puedo hacer? "
     "Gracias."),
    ("ORD-1006", "Ethan Brooks", "ethan.b@example.com", "Boom Arm + Pop Filter", 34.50, 8, "delivered", 3,
     "email", "Missing piece",
     "The pop filter was missing from the box. The boom arm is fine."),
    ("ORD-1007", "Hannah Lee", "hannah.lee@example.com", "Studio Headphones", 129.00, 6, "delivered", 2,
     "paypal_dispute", "Significantly not as described",
     "These are a different color than the listing and the left ear cup has a dent. This is ridiculous!! I paid "
     "full price."),
    ("ORD-1008", "Omar Haddad", "omar.h@example.com", "Wireless Lavalier Mic (2-pack)", 59.99, 15, "delivered", 11,
     "email", "Question about my order",
     "Hi, something is off with my order, can someone get back to me?"),
    ("ORD-1009", "Grace Kim", "grace.kim@example.com", "Ring Light 12in", 27.99, 20, "delivered", 16,
     "email", "Ordered by mistake",
     "I ordered this by mistake, it's still sealed. Can I return it?"),
    ("ORD-1010", "Daniel Novak", "daniel.novak@example.com", "Podcast Starter Kit", 149.00, 14, "delivered", 9,
     "email", "Mixer stopped working",
     "The mixer in the kit stopped working after two days. It won't power on with any cable. Really "
     "disappointed."),
    ("ORD-1011", "Maya Thompson", "maya.t@example.com", "Clip-on Mic - Black", 49.99, 40, "delivered", 36,
     "email", "Another issue",
     "Sorry to write again. This mic has a scuff on the receiver. It works, just looks used."),
    ("ORD-1012", "Chloe Dubois", "chloe.d@example.com", "Clip-on Mic - White", 39.99, 30, "lost", None,
     "paypal_dispute", "Never arrived",
     "Never received my order. Tracking says the package is lost."),
]


def seed() -> dict:
    db.reset()
    modes = {"sandbox": 0, "demo": 0}
    errors = []
    for (oid, name, email, item, amount, ordered, delivery, delivered, channel, subject, message) in SAMPLES:
        try:
            capture_id, mode = paypal.create_captured_payment(oid, item, amount, name)
        except paypal.PayPalError as e:
            errors.append(f"{oid}: {e}")
            capture_id, mode = f"DEMO-CAP-{oid}", "demo"
        modes[mode] += 1
        db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (oid, name, email, item, amount, "USD", _d(ordered), delivery, _d(delivered), capture_id, mode))
        db.execute("INSERT INTO cases (order_id, channel, subject, message, received_at, updated_at) "
                   "VALUES (?,?,?,?,?,?)", (oid, channel, subject, message, db.now(), db.now()))
    return {"cases": len(SAMPLES), "payments": modes, "errors": errors[:3]}
