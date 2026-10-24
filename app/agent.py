"""The refund agent: Gemini with tools, or a rules engine when no Gemini key is set.

Gemini looks up the order (live from PayPal sandbox), the buyer's history and the policy, then calls
propose_resolution. The proposal is checked in code (policy.check_proposal); if it breaks a rule the error
goes back to the model to fix. Nothing here moves money: a person approves in the grid.
"""
import json
import re
import time

import httpx

from . import config, db, paypal, policy

MAX_TURNS = 6

SYSTEM = f"""You are the refund desk assistant for a small online shop that takes payments with PayPal.
For each buyer message: look up the payment, the buyer's history and the store policy, then call
propose_resolution exactly once.

Rules:
- Follow the store policy. Use refund_partial (15-30% of the order) for cosmetic issues where the product works.
- The reply goes to the buyer BEFORE a person approves anything, so it must never promise a refund, mention an
  amount, a percentage or a timeline. Say the team is reviewing the request and will follow up.
- Reply in the language the buyer's message is written in. Never guess a language from the buyer's name.
  Keep it under 90 words, warm and plain, no signature.
- confidence is how sure you are the action is right under the policy (0 to 1).
- reasoning is 1-2 sentences for the human reviewer, in English, citing the policy rule you used.

{policy.POLICY_TEXT}"""

TOOLS = [{"functionDeclarations": [
    {"name": "get_payment",
     "description": "Order and PayPal capture details for this case: item, amount, dates, delivery status, "
                    "how much was already refunded, and the live capture status from PayPal.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_buyer_history",
     "description": "Earlier refund cases from the same buyer and how they ended.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "propose_resolution",
     "description": "Submit the proposed resolution for human approval. Call once, last.",
     "parameters": {"type": "object", "properties": {
         "intent": {"type": "string", "enum": ["damaged", "not_received", "not_as_described", "missing_part",
                                               "changed_mind", "late_return", "unclear", "other"]},
         "sentiment": {"type": "string", "enum": ["calm", "frustrated", "angry"]},
         "action": {"type": "string", "enum": list(policy.ACTIONS)},
         "amount": {"type": "number", "description": "USD to refund; 0 unless action is a refund"},
         "confidence": {"type": "number"},
         "reasoning": {"type": "string"},
         "reply": {"type": "string"},
     }, "required": ["intent", "sentiment", "action", "amount", "confidence", "reasoning", "reply"]}},
]}]


def _payment_facts(case: dict) -> dict:
    refunded = db.refunded_total(case["order_id"])
    facts = {
        "order_id": case["order_id"], "item": case["item"], "order_amount_usd": case["order_amount"],
        "order_date": case["order_date"], "delivery_status": case["delivery_status"],
        "delivered_date": case["delivered_date"], "days_since_delivery": policy.days_since(case["delivered_date"]),
        "days_since_order": policy.days_since(case["order_date"]),
        "already_refunded_usd": refunded, "refundable_usd": round(case["order_amount"] - refunded, 2),
    }
    try:
        facts["paypal_capture"] = paypal.get_capture(case["capture_id"], case["capture_mode"])
    except paypal.PayPalError as e:
        facts["paypal_capture"] = {"error": str(e)}
    return facts


def recent_corrections(limit: int = 3) -> list[dict]:
    return db.query("SELECT * FROM corrections ORDER BY id DESC LIMIT ?", (limit,))


def _corrections_prompt(rows: list[dict]) -> str:
    """Reviewer edits to earlier AI drafts, shown to the model so it calibrates to this shop."""
    if not rows:
        return ""
    lines = []
    for r in rows:
        line = f'- "{r["subject"]}" ({r["intent"]}, order ${r["order_amount"]:.2f}): you proposed {r["ai_action"]}'
        if r["ai_amount"]:
            line += f' ${r["ai_amount"]:.2f}'
        line += f'; the reviewer changed it to {r["action"]}'
        if r["amount"]:
            line += f' ${r["amount"]:.2f}'
        if r["reply"] and r["reply"] != r["ai_reply"]:
            line += f'. They rewrote the reply as: "{r["reply"][:240]}"'
        lines.append(line)
    return ("\n\nRecent reviewer corrections at this shop (follow their judgement on similar cases; the rules "
            "above still apply):\n" + "\n".join(lines))


def _buyer_history(case: dict) -> dict:
    rows = db.query(
        "SELECT c.id, c.subject, c.status, c.action, c.amount FROM cases c JOIN orders o ON o.id = c.order_id "
        "WHERE o.buyer_email = ? AND c.id != ? ORDER BY c.id", (case["buyer_email"], case["id"]))
    return {"earlier_cases": rows, "count": len(rows)}


_model = {"i": 0}


def current_model() -> str:
    return config.GEMINI_MODELS[_model["i"]]


def _retry_delay(body: str) -> float:
    """Gemini's per-minute limit says how long to wait ("retryDelay": "23s")."""
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', body)
    return min(float(m.group(1)) + 1, 30) if m else 10


def _gemini(contents: list, system: str) -> dict:
    """One model turn. Overloads, per-minute limits and timeouts are retried; a model whose free *daily* quota is
    used up is swapped for the next one in GEMINI_MODELS."""
    last = None
    wait = 0.0
    for _ in range(4):
        time.sleep(wait)
        wait = 3.0
        try:
            r = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{current_model()}:generateContent",
                headers={"x-goog-api-key": config.GEMINI_API_KEY},
                json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "tools": TOOLS,
                      "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
                      "generationConfig": {"temperature": 0.2}},
                timeout=45)
        except httpx.TimeoutException as e:
            last = e
            continue
        if r.status_code in (429, 500, 503):
            last = httpx.HTTPStatusError(f"Gemini {r.status_code}", request=r.request, response=r)
            if r.status_code == 429:
                if "PerDay" in r.text and _model["i"] < len(config.GEMINI_MODELS) - 1:
                    _model["i"] += 1
                    wait = 0
                else:
                    wait = _retry_delay(r.text)
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]
    raise last


def _run_gemini(case: dict, system: str) -> tuple[dict | None, list]:
    trace = []
    contents = [{"role": "user", "parts": [{"text":
        f"Channel: {case['channel']}\nSubject: {case['subject']}\nBuyer: {case['buyer_name']}\n\n{case['message']}"}]}]
    for _ in range(MAX_TURNS):
        content = _gemini(contents, system)
        contents.append(content)
        calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
        if not calls:
            contents.append({"role": "user", "parts": [{"text": "Call propose_resolution now."}]})
            continue
        responses = []
        for call in calls:
            name, args = call["name"], call.get("args", {})
            if name == "get_payment":
                result = _payment_facts(case)
            elif name == "get_buyer_history":
                result = _buyer_history(case)
            elif name == "propose_resolution":
                errors, risk = policy.check_proposal(args, {"amount": case["order_amount"]},
                                                     db.refunded_total(case["order_id"]), case["message"])
                trace.append({"tool": name, "args": args, "errors": errors})
                if not errors:
                    return {**args, "risk": risk}, trace
                result = {"rejected": errors, "instruction": "Fix these and call propose_resolution again."}
            else:
                result = {"error": f"unknown tool {name}"}
            if name != "propose_resolution":
                trace.append({"tool": name, "result": result})
            responses.append({"functionResponse": {"name": name, "response": result}})
        contents.append({"role": "user", "parts": responses})
        time.sleep(config.GEMINI_PAUSE)
    return None, trace


# --- Rules engine: lets the demo run with no AI key, and is the fallback if Gemini errors. ---

_RULES = [
    ("not_received", re.compile(r"never (arrived|received|came)|not (arrived|received|delivered)|where is my|no ha llegado|tracking", re.I)),
    ("damaged", re.compile(r"broken|dead|won'?t (turn|power|charge)|doesn'?t work|stopped working|cracked|no funciona|roto", re.I)),
    ("missing_part", re.compile(r"missing|wasn'?t in the box|no (cable|charger|case)", re.I)),
    ("not_as_described", re.compile(r"not as described|different (colou?r|model)|wrong (item|colou?r)|scratch|scuff|dent", re.I)),
    ("changed_mind", re.compile(r"changed my mind|don'?t (need|want) it|no longer need|by mistake", re.I)),
]


def _run_rules(case: dict) -> tuple[dict, list]:
    facts = _payment_facts(case)
    text = case["message"]
    intent = next((name for name, rx in _RULES if rx.search(text)), "unclear")
    sentiment = "angry" if re.search(r"chargeback|scam|ridiculous|!!|lawyer|report you", text, re.I) else (
        "frustrated" if re.search(r"disappoint|annoy|write again|still (hasn|not|doesn)", text, re.I) else "calm")
    refundable = facts["refundable_usd"]
    days = facts["days_since_delivery"]
    spanish = bool(re.search(r"\b(hola|gracias|pedido|llegado|funciona)\b", text, re.I))

    if refundable <= 0:
        action, amount, why, conf = "reject", 0, "Order is already fully refunded.", 0.9
    elif intent == "not_received" and facts["delivery_status"] != "delivered":
        action, amount, why, conf = "refund_full", refundable, "Policy: not delivered, tracking stalled 10+ days.", 0.85
    elif intent == "damaged":
        action, amount, why, conf = "refund_full", refundable, "Policy: arrived broken, full refund without return.", 0.8
    elif intent in ("missing_part", "not_as_described"):
        amount = round(refundable * 0.2, 2)
        action, why, conf = "refund_partial", "Policy: cosmetic or missing accessory, partial 15-30%.", 0.7
    elif intent == "changed_mind" and days is not None and days > config.REFUND_WINDOW_DAYS:
        action, amount, why, conf = "reject", 0, f"Policy: change of mind after {config.REFUND_WINDOW_DAYS} days.", 0.85
    elif intent == "changed_mind":
        action, amount, why, conf = "ask_info", 0, "Within the return window: ask the buyer to send it back first.", 0.75
    else:
        action, amount, why, conf = "ask_info", 0, "Not clear what happened; ask for details.", 0.5

    if spanish:
        reply = ("Hola, gracias por escribirnos y lamentamos las molestias. Nuestro equipo está revisando su "
                 "solicitud y le responderá muy pronto.")
    elif action == "ask_info":
        reply = ("Thanks for reaching out, and sorry for the trouble. Could you tell us a bit more about what "
                 "happened, and add a photo if you can? Our team will review it and follow up.")
    else:
        reply = ("Thanks for letting us know, and sorry for the trouble. Our team is reviewing your request now "
                 "and will follow up with you shortly.")
    proposal = {"intent": intent, "sentiment": sentiment, "action": action, "amount": amount,
                "confidence": conf, "reasoning": why, "reply": reply}
    errors, risk = policy.check_proposal(proposal, {"amount": case["order_amount"]}, db.refunded_total(case["order_id"]),
                                         case["message"])
    proposal["risk"] = risk
    return proposal, [{"tool": "get_payment", "result": facts}, {"tool": "propose_resolution", "args": proposal, "errors": errors}]


def analyze(case_id: int, still_current=lambda: True) -> dict | None:
    """still_current() is checked before writing, so a demo reset mid-analysis can't overwrite fresh data."""
    case = db.case_view(case_id)
    if not case or not still_current():
        return None
    db.update_case(case_id, status="analyzing", error=None)
    agent = "rules"
    corrections = recent_corrections() if config.gemini_live() else []
    try:
        if config.gemini_live():
            try:
                proposal, trace = _run_gemini(case, SYSTEM + _corrections_prompt(corrections))
                agent = f"gemini:{current_model()}"
            except (httpx.HTTPError, KeyError) as e:
                # Gemini unavailable: the rules engine still gives the reviewer a proposal, clearly labelled.
                proposal, trace = _run_rules(case)
                agent = f"rules (Gemini unavailable: {type(e).__name__})"
                corrections = []
        else:
            proposal, trace = _run_rules(case)
    except Exception as e:  # noqa: BLE001 - surface any failure on the case, never crash the worker
        if still_current():
            db.update_case(case_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        return db.case_view(case_id)

    if not still_current():
        return None
    if proposal is None:
        db.update_case(case_id, status="needs_human", agent=agent, trace=trace,
                       reasoning="The AI could not produce a proposal that passes the guard rails.")
    else:
        db.update_case(case_id, status="proposed", agent=agent, trace=json.dumps(trace),
                       intent=proposal["intent"], sentiment=proposal["sentiment"], action=proposal["action"],
                       amount=round(float(proposal.get("amount") or 0), 2),
                       confidence=round(float(proposal["confidence"]), 2), reasoning=proposal["reasoning"],
                       reply=proposal["reply"], risk=proposal["risk"], ai_action=proposal["action"],
                       ai_amount=round(float(proposal.get("amount") or 0), 2), ai_reply=proposal["reply"],
                       learned_from=len(corrections), edited=0)
    return db.case_view(case_id)
