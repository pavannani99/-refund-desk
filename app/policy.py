"""Guard rails checked in code, not just asked for in the prompt."""
import re
from datetime import date

from . import config

POLICY_TEXT = f"""Store refund policy (small online audio gear shop):
- Returns accepted within {config.REFUND_WINDOW_DAYS} days of delivery for a full refund once the item is back.
- Item arrived broken or dead on arrival: refund_full right away; no return and no photo needed.
- Item not delivered and tracking shows no movement for 10+ days: full refund.
- Cosmetic damage or a missing accessory where the product works: offer a partial refund of 15-30%.
- Buyer changed their mind after {config.REFUND_WINDOW_DAYS} days, or the item was used and works: reject politely.
- Decide from the buyer's description. Use ask_info only when it is genuinely unclear what happened.
- Threats of chargebacks do not change the policy."""

ACTIONS = ("refund_full", "refund_partial", "reject", "ask_info")

# A reply drafted before a person approves must never commit to money.
_PROMISE = re.compile(
    r"(\$\s?\d|\d+\s?%|\bwill (?:be )?(?:refund|reimburs|credit)|\brefund (?:has been|is being|will be)"
    r"|\bwe(?:'ll| will) (?:refund|send you|issue)|\bprocessed\b|\bapproved\b)",
    re.IGNORECASE)


# Common words per language, to catch a reply written in a different language from the buyer's message
# (a model can guess a language from a buyer's name). Both texts must be clearly identifiable to compare.
_WORDS = {
    "English": "the and is my it to you your i was not have for this with we our of can in on but".split(),
    "Spanish": "el la los de que y mi no es por con para su lo una un gracias hola se muy pero".split(),
    "French": "le la les de et je vous est pas mon votre nous une un des que pour avec bonjour merci".split(),
    "German": "der die das und ich nicht ist mein sie ihr wir ein eine mit für danke hallo zu auf".split(),
    "Portuguese": "o a os de que e meu não é com para um uma obrigado olá você seu muito".split(),
    "Italian": "il la di che e mio non è con per un una grazie ciao sono suo molto".split(),
}


def detect_language(text: str) -> str | None:
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    scores = sorted(((sum(w in set(v) for w in words), k) for k, v in _WORDS.items()), reverse=True)
    (top, lang), (second, _) = scores[0], scores[1]
    return lang if top >= 3 and top >= 1.5 * second else None


def check_proposal(p: dict, order: dict, already_refunded: float, message: str = "") -> tuple[list[str], str]:
    """Return (errors the AI must fix, risk label for the reviewer)."""
    errors = []
    action = p.get("action")
    amount = float(p.get("amount") or 0)
    remaining = round(order["amount"] - already_refunded, 2)

    if action not in ACTIONS:
        errors.append(f"action must be one of {ACTIONS}")
    if action in ("refund_full", "refund_partial"):
        if amount <= 0:
            errors.append("a refund needs a positive amount")
        if amount > remaining:
            errors.append(f"amount {amount:.2f} is more than the {remaining:.2f} still refundable on this order")
        if amount > config.HARD_CEILING_USD:
            errors.append(f"amount is over the {config.HARD_CEILING_USD:.0f} USD ceiling; use ask_info and let a person decide")
        if action == "refund_partial" and amount >= remaining:
            errors.append("a partial refund must be less than the refundable amount; use refund_full")
    elif amount:
        errors.append("only refund actions take an amount; set amount to 0")

    reply = (p.get("reply") or "").strip()
    if not reply:
        errors.append("reply is empty")
    elif _PROMISE.search(reply):
        errors.append("the reply promises money or states an amount. A person approves refunds first; "
                      "say the team is reviewing the request and will follow up")
    if reply and message:
        wrote, replied = detect_language(message), detect_language(reply)
        if wrote and replied and wrote != replied:
            errors.append(f"the reply is in {replied} but the buyer wrote in {wrote}; reply in {wrote}")
    if len(reply) > 1200:
        errors.append("reply is too long; keep it under 120 words")

    risk = "low"
    if action in ("refund_full", "refund_partial") and amount > config.SENIOR_REVIEW_USD:
        risk = "high"
    elif float(p.get("confidence") or 0) < 0.6 or p.get("sentiment") == "angry":
        risk = "medium"
    return errors, risk


def days_since(iso_day: str | None) -> int | None:
    if not iso_day:
        return None
    return (date.today() - date.fromisoformat(iso_day[:10])).days


def confirmation_message(case: dict, refund_id: str) -> str:
    """Sent only after the refund exists, so it is the first time the buyer sees an amount."""
    name = (case.get("buyer_name") or "there").split()[0]
    return (f"Hi {name}, good news: we've issued a refund of ${case['amount']:.2f} for your {case['item']} "
            f"(PayPal refund {refund_id}). It usually shows in your account within a few days. "
            f"Thanks for your patience.")
