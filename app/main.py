import json
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, config, db, paypal, policy, seed

STATIC = Path(__file__).resolve().parent.parent / "static"

# One worker analyzes cases in order, so the free Gemini tier is never hit in parallel.
_jobs: "queue.Queue[int]" = queue.Queue()


def _worker():
    while True:
        case_id = _jobs.get()
        try:
            agent.analyze(case_id)
        finally:
            _jobs.task_done()


def _queue_new():
    for row in db.query("SELECT id FROM cases WHERE status = 'new' ORDER BY id"):
        db.update_case(row["id"], status="queued")
        _jobs.put(row["id"])


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_worker, daemon=True).start()
    if not db.one("SELECT id FROM cases LIMIT 1"):
        seed.seed()
    db.execute("UPDATE cases SET status = 'new' WHERE status IN ('queued', 'analyzing')")
    _queue_new()
    yield


app = FastAPI(title="Refund Desk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"paypal": "sandbox" if config.paypal_live() else "demo",
            "ai": f"gemini:{config.GEMINI_MODEL}" if config.gemini_live() else "rules",
            "queue": _jobs.qsize(),
            "limits": {"senior_review_usd": config.SENIOR_REVIEW_USD, "hard_ceiling_usd": config.HARD_CEILING_USD,
                       "refund_window_days": config.REFUND_WINDOW_DAYS}}


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


@app.patch("/api/cases/{case_id}")
def edit_case(case_id: int, edit: CaseEdit):
    case = db.case_view(case_id)
    if not case:
        raise HTTPException(404, "case not found")
    if case["status"] in ("refunded", "rejected", "replied"):
        raise HTTPException(409, f"case is already {case['status']}")
    changes = {k: v for k, v in edit.model_dump().items() if v is not None}
    if changes.get("action") in ("reject", "ask_info"):
        changes["amount"] = 0
    merged = {**case, **changes}
    errors, risk = policy.check_proposal(merged, {"amount": case["order_amount"]}, db.refunded_total(case["order_id"]))
    if errors:
        raise HTTPException(422, "; ".join(errors))
    db.update_case(case_id, **changes, risk=risk, edited=1)
    return db.case_view(case_id)


class Ids(BaseModel):
    ids: list[int]


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
            errors, _ = policy.check_proposal(case, {"amount": case["order_amount"]}, db.refunded_total(case["order_id"]))
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
                           final_message=policy.confirmation_message(case, ref["id"]))
            results.append({"id": case_id, "ok": True, "refund_id": ref["id"], "source": ref["source"]})
        else:
            db.update_case(case_id, status="replied" if case["action"] == "ask_info" else "rejected",
                           final_message=case["reply"], error=None)
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
                                                          "and aren't able to offer a refund on this order.")
    return {"ok": True}


@app.post("/api/cases/reanalyze")
def reanalyze(body: Ids):
    for case_id in body.ids:
        case = db.case_view(case_id)
        if case and case["status"] not in ("refunded", "rejected", "replied", "queued", "analyzing"):
            db.update_case(case_id, status="queued")
            _jobs.put(case_id)
    return {"queued": len(body.ids)}


class NewCase(BaseModel):
    order_id: str
    subject: str
    message: str


@app.post("/api/cases")
def new_case(body: NewCase):
    if not db.one("SELECT id FROM orders WHERE id = ?", (body.order_id,)):
        raise HTTPException(404, "unknown order id")
    case_id = db.execute("INSERT INTO cases (order_id, channel, subject, message, received_at, status, updated_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (body.order_id, "email", body.subject[:200], body.message[:4000], db.now(), "queued",
                          db.now()))
    _jobs.put(case_id)
    return {"id": case_id}


@app.get("/api/orders")
def orders():
    return db.query("SELECT id, buyer_name, item, amount FROM orders ORDER BY id")


@app.post("/api/reset")
def reset():
    while not _jobs.empty():
        _jobs.get_nowait()
        _jobs.task_done()
    result = seed.seed()
    _queue_new()
    return result
