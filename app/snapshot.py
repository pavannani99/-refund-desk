"""A saved run of the real Gemini agent over the sample cases.

The hosted demo restarts often (free tier), and re-analyzing every sample on each start would use up the free AI
quota and keep judges waiting. So the samples load with proposals Gemini actually made, recorded with:

    python -m app.snapshot

New buyer messages typed into the demo are still analyzed live.
"""
import json
import time
from pathlib import Path

from . import agent, config, db, seed

PATH = Path(__file__).resolve().parent / "demo_snapshot.json"
FIELDS = ("intent", "sentiment", "confidence", "action", "amount", "reasoning", "reply", "risk", "agent", "trace")


def apply() -> int:
    """Copy saved proposals onto freshly seeded cases. Returns how many cases were filled."""
    if not PATH.exists():
        return 0
    saved = {row["order_id"]: row for row in json.loads(PATH.read_text(encoding="utf-8"))}
    filled = 0
    for case in db.query(db.CASE_SELECT + " WHERE c.status = 'new'"):
        row = saved.get(case["order_id"])
        if not row:
            continue
        trace = row["trace"]
        for step in trace:  # point the recorded lookups at this run's PayPal capture
            cap = (step.get("result") or {}).get("paypal_capture")
            if isinstance(cap, dict):
                cap.update(id=case["capture_id"], source="paypal_sandbox" if case["capture_mode"] == "sandbox" else "demo")
        fields = {k: row[k] for k in FIELDS}
        fields["trace"] = json.dumps(trace)
        fields["agent"] = f"{row['agent']} (saved run)"
        db.update_case(case["id"], status="proposed", ai_action=row["action"], ai_amount=row["amount"],
                       ai_reply=row["reply"], **fields)
        filled += 1
    return filled


def record():
    if not config.gemini_live():
        raise SystemExit("Set GEMINI_API_KEY in .env first: the snapshot must come from the real model.")
    seed.seed()
    rows = []
    for case in db.query("SELECT id FROM cases ORDER BY id"):
        for attempt in range(3):
            result = agent.analyze(case["id"])
            print(case["id"], result["status"], result["agent"], result["action"], result["amount"], flush=True)
            if (result["agent"] or "").startswith("gemini:"):
                break
            time.sleep(30)
        else:
            raise SystemExit(f"Case {case['id']} kept falling back to {result['agent']}; try again later.")
        row = {k: result[k] for k in FIELDS}
        row["trace"] = json.loads(row["trace"])
        rows.append({"order_id": result["order_id"], **row})
    PATH.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(rows)} proposals to {PATH.name}")


if __name__ == "__main__":
    record()
