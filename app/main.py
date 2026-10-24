import csv
import io
import json
import queue
import uuid
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, config, db, paypal, policy, seed, snapshot

STATIC = Path(__file__).resolve().parent.parent / "static"
DONE = ("refunded", "rejected", "replied")

# One worker analyzes cases in order, so the free Gemini tier is never hit in parallel. Each job carries the data
# generation it was queued in; a demo reset bumps the generation so older jobs are dropped.
_jobs: "queue.Queue[tuple[int, int]]" = queue.Queue()
_gen = {"n": 0}
_live_ai: deque = deque()   # timestamps of live AI analyses, for the hourly cap
_last_reset = {"t": 0.0}


def _worker():
    while True:
        case_id, gen = _jobs.get()
        try:
            agent.analyze(case_id, still_current=lambda g=gen: g == _gen["n"])
        except Exception:  # noqa: BLE001 - keep the worker alive whatever one case does
            pass
        finally:
            _jobs.task_done()


def _enqueue(case_id: int):
    db.update_case(case_id, status="queued")
    _jobs.put((case_id, _gen["n"]))


def _take_live_ai_slot():
    now = time.time()
    while _live_ai and now - _live_ai[0] > 3600:
        _live_ai.popleft()
    if config.gemini_live() and len(_live_ai) >= config.LIVE_AI_PER_HOUR:
        raise HTTPException(429, f"This public demo analyzes up to {config.LIVE_AI_PER_HOUR} new messages an hour "
                                 "to stay on the free AI tier. Please try again a little later.")
    _live_ai.append(now)


def _load_demo_data():
    seed.seed()
    snapshot.apply()
    for row in db.query("SELECT id FROM cases WHERE status = 'new' ORDER BY id"):
        _enqueue(row["id"])


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_worker, daemon=True).start()
    if not db.one("SELECT id FROM cases LIMIT 1"):
        _load_demo_data()
    else:
        for row in db.query("SELECT id FROM cases WHERE status IN ('new', 'queued', 'analyzing') ORDER BY id"):
            _enqueue(row["id"])
    yield


app = FastAPI(title="Refund Desk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    modes = {r["capture_mode"]: r["n"] for r in db.query("SELECT capture_mode, COUNT(*) AS n FROM orders GROUP BY capture_mode")}
    return {"paypal": "sandbox" if config.paypal_live() else "demo",
            "payments": modes,
            "ai": f"gemini:{agent.current_model()}" if config.gemini_live() else "rules",
            "queue": _jobs.qsize(),
            "corrections": db.one("SELECT COUNT(*) AS n FROM corrections")["n"],
            "policy": policy.POLICY_TEXT,
            "limits": {"senior_review_usd": config.SENIOR_REVIEW_USD, "hard_ceiling_usd": config.HARD_CEILING_USD,
                       "refund_window_days": config.REFUND_WINDOW_DAYS, "live_ai_per_hour": config.LIVE_AI_PER_HOUR}}


@app.get("/api/cases")
def list_cases():
    rows = db.query(db.CASE_SELECT + " ORDER BY c.id")
    for r in rows:
        r["trace"] = json.loads(r["trace"]) if r["trace"] else []
        r["refundable"] = round(r["order_amount"] - db.refunded_total(r["order_id"]), 2)
    return rows


class CaseEdit(BaseModel):
    action: str | None = None
    amount: float | None = None
    reply: str | None = None


def _record_correction(case: dict):
    """Keep one correction per case: what the AI drafted vs. what the reviewer changed it to."""
    db.execute("DELETE FROM corrections WHERE case_id = ?", (case["id"],))
    if not case["ai_action"]:
        return
    if (case["action"], round(case["amount"] or 0, 2), case["reply"]) == (
            case["ai_action"], round(case["ai_amount"] or 0, 2), case["ai_reply"]):
        return
    db.execute("INSERT INTO corrections (case_id, intent, subject, ai_action, ai_amount, ai_reply, action, amount, "
               "reply, order_amount, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (case["id"], case["intent"], case["subject"], case["ai_action"], case["ai_amount"], case["ai_reply"],
                case["action"], case["amount"], case["reply"], case["order_amount"], db.now()))


@app.patch("/api/cases/{case_id}")
def edit_case(case_id: int, edit: CaseEdit):
    case = db.case_view(case_id)
    if not case:
        raise HTTPException(404, "case not found")
    if case["status"] != "proposed":
        raise HTTPException(409, f"only proposed cases can be edited (this one is {case['status']})")
    changes = {k: v for k, v in edit.model_dump().items() if v is not None}
    if changes.get("action") in ("reject", "ask_info"):
        changes["amount"] = 0
    merged = {**case, **changes}
    errors, risk = policy.check_proposal(merged, {"amount": case["order_amount"]}, db.refunded_total(case["order_id"]),
                                         case["message"])
    if errors:
        raise HTTPException(422, "; ".join(errors))
    db.update_case(case_id, **changes, risk=risk, edited=1)
    updated = db.case_view(case_id)
    _record_correction(updated)
    return updated


class Ids(BaseModel):
    ids: list[int]


def _decided_by(case: dict) -> str:
    return "Reviewer (edited the AI draft)" if case["edited"] else "Reviewer (AI draft as proposed)"


@app.post("/api/cases/approve")
def approve(body: Ids):
    """The human step. Refund actions call PayPal; the others mark the reply as sent."""
    results = []
    for case_id in body.ids:
        case = db.case_view(case_id)
        if not case or case["status"] != "proposed":
            results.append({"id": case_id, "ok": False, "error": "only proposed cases can be approved"})
            continue
        if case["action"] in ("refund_full", "refund_partial"):
            errors, _ = policy.check_proposal(case, {"amount": case["order_amount"]}, db.refunded_total(case["order_id"]),
                                              case["message"])
            if errors:
                results.append({"id": case_id, "ok": False, "error": "; ".join(errors)})
                continue
            try:
                ref = paypal.refund_capture(case["capture_id"], case["capture_mode"], case["amount"], case_id,
                                            f"Refund for {case['item']}")
            except paypal.PayPalError as e:
                db.update_case(case_id, error=str(e)[:500])
                results.append({"id": case_id, "ok": False, "error": str(e)})
                continue
            db.update_case(case_id, status="refunded", refund_id=ref["id"], error=None,
                           final_message=policy.confirmation_message(case, ref["id"]),
                           decided_by=_decided_by(case), decided_at=db.now())
            results.append({"id": case_id, "ok": True, "refund_id": ref["id"], "source": ref["source"]})
        else:
            db.update_case(case_id, status="replied" if case["action"] == "ask_info" else "rejected",
                           final_message=case["reply"], error=None, decided_by=_decided_by(case),
                           decided_at=db.now())
            results.append({"id": case_id, "ok": True})
    return {"results": results}


@app.post("/api/cases/decline")
def decline(body: Ids):
    """Reviewer overrides the AI: no refund, send a polite reply."""
    for case_id in body.ids:
        case = db.case_view(case_id)
        if case and case["status"] in ("proposed", "needs_human", "error"):
            db.update_case(case_id, status="rejected", action="reject", amount=0,
                           final_message=case["reply"] or "Thanks for reaching out. We've reviewed your request "
                                                          "and aren't able to offer a refund on this order.",
                           decided_by="Reviewer (overrode the AI: declined)", decided_at=db.now())
    return {"ok": True}


@app.post("/api/cases/reanalyze")
def reanalyze(body: Ids):
    queued = 0
    for case_id in body.ids:
        case = db.case_view(case_id)
        if case and case["status"] not in (*DONE, "queued", "analyzing"):
            _take_live_ai_slot()
            _enqueue(case_id)
            queued += 1
    return {"queued": queued}


@app.get("/api/cases/{case_id}/refund-status")
def refund_status(case_id: int):
    """PayPal's own record of the refund this case issued."""
    case = db.case_view(case_id)
    if not case or not case["refund_id"]:
        raise HTTPException(404, "this case has no refund")
    try:
        return paypal.get_refund(case["refund_id"], case["capture_mode"])
    except paypal.PayPalError as e:
        raise HTTPException(502, str(e))


class NewCase(BaseModel):
    order_id: str
    subject: str
    message: str


@app.post("/api/cases")
def new_case(body: NewCase):
    if not db.one("SELECT id FROM orders WHERE id = ?", (body.order_id,)):
        raise HTTPException(404, "unknown order id")
    if not body.message.strip():
        raise HTTPException(422, "the message is empty")
    _take_live_ai_slot()
    case_id = db.execute("INSERT INTO cases (order_id, channel, subject, message, received_at, updated_at) "
                         "VALUES (?,?,?,?,?,?)",
                         (body.order_id, "email", body.subject[:200], body.message[:4000], db.now(), db.now()))
    _enqueue(case_id)
    return {"id": case_id}


class CsvImport(BaseModel):
    csv: str


IMPORT_MAX_ROWS = 25
IMPORT_REQUIRED = ("buyer_name", "item", "amount", "subject", "message")


@app.post("/api/import")
def import_csv(body: CsvImport):
    """Bulk-load refund requests. Each row becomes a case for the AI; new orders get a sandbox payment."""
    reader = csv.DictReader(io.StringIO(body.csv.lstrip("﻿")))
    missing = [c for c in IMPORT_REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise HTTPException(422, f"the CSV is missing column(s): {', '.join(missing)}")
    rows = list(reader)
    if not rows:
        raise HTTPException(422, "the CSV has no rows")
    if len(rows) > IMPORT_MAX_ROWS:
        raise HTTPException(413, f"import up to {IMPORT_MAX_ROWS} rows at a time")

    imported, errors, waiting = 0, [], 0
    for n, raw in enumerate(rows, start=2):  # row 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        try:
            if any(not row.get(c) for c in IMPORT_REQUIRED):
                raise ValueError("buyer_name, item, amount, subject and message are all required")
            try:
                amount = round(float(row["amount"].replace("$", "").replace(",", "")), 2)
            except ValueError:
                raise ValueError(f"amount '{row['amount']}' is not a number") from None
            if not 0 < amount <= config.HARD_CEILING_USD:
                raise ValueError(f"amount must be between 0.01 and {config.HARD_CEILING_USD:.0f}")
            channel = row.get("channel") or "email"
            if channel not in ("email", "paypal_dispute"):
                raise ValueError("channel must be email or paypal_dispute")
            delivery = row.get("delivery_status") or "delivered"
            oid = (row.get("order_id") or f"IMP-{uuid.uuid4().hex[:6].upper()}")[:40]
            if not db.one("SELECT id FROM orders WHERE id = ?", (oid,)):
                seed.add_order(oid, row["buyer_name"][:100], row.get("buyer_email", "")[:200], row["item"][:200],
                               amount, row.get("order_date") or None, delivery, row.get("delivered_date") or None)
            case_id = seed.add_case(oid, channel, row["subject"][:200], row["message"][:4000])
        except ValueError as e:
            errors.append({"row": n, "error": str(e)})
            continue
        imported += 1
        try:
            _take_live_ai_slot()
            _enqueue(case_id)
        except HTTPException:
            waiting += 1  # over the hourly AI cap: stays 'new' until someone clicks Re-run AI
    return {"imported": imported, "errors": errors, "waiting_for_ai": waiting}


@app.get("/api/orders")
def orders():
    return db.query("SELECT id, buyer_name, item, amount FROM orders ORDER BY id")


@app.post("/api/reset")
def reset():
    if time.time() - _last_reset["t"] < 15:
        raise HTTPException(429, "The demo was just reset. Give it a few seconds.")
    _last_reset["t"] = time.time()
    _gen["n"] += 1
    _load_demo_data()
    return {"ok": True}
