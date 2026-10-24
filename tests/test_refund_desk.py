import os

os.environ["DB_PATH"] = ":memory:"
os.environ["PAYPAL_CLIENT_ID"] = ""
os.environ["GEMINI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, db, policy, seed  # noqa: E402
from app.main import app  # noqa: E402

ORDER = {"amount": 50.0}


def proposal(**kw):
    base = {"action": "refund_full", "amount": 50.0, "confidence": 0.9, "sentiment": "calm",
            "reply": "Thanks for letting us know. Our team is reviewing your request and will follow up."}
    return {**base, **kw}


def test_a_clean_proposal_passes():
    assert policy.check_proposal(proposal(), ORDER, 0) == ([], "low")


def test_refund_over_what_is_left_is_refused():
    errors, _ = policy.check_proposal(proposal(amount=30), ORDER, already_refunded=25)
    assert any("still refundable" in e for e in errors)


def test_reply_that_promises_money_is_refused():
    for reply in ("We will refund you $50 today.", "Your refund has been processed.", "We'll issue 50% back."):
        errors, _ = policy.check_proposal(proposal(reply=reply), ORDER, 0)
        assert errors, reply


def test_partial_refund_must_be_partial_and_non_refunds_take_no_amount():
    assert policy.check_proposal(proposal(action="refund_partial", amount=50), ORDER, 0)[0]
    assert policy.check_proposal(proposal(action="reject", amount=10), ORDER, 0)[0]


def test_large_refunds_are_flagged_high_risk():
    _, risk = policy.check_proposal(proposal(amount=149), {"amount": 149.0}, 0)
    assert risk == "high"


def test_rules_agent_follows_policy_on_the_samples():
    seed.seed()
    got = {c["order_id"]: agent.analyze(c["id"]) for c in db.query("SELECT id, order_id FROM cases")}
    assert got["ORD-1001"]["action"] == "refund_full"       # dead on arrival
    assert got["ORD-1002"]["action"] == "refund_full"       # not delivered, chargeback threat
    assert got["ORD-1003"]["action"] == "refund_partial"    # cosmetic scratch
    assert got["ORD-1004"]["action"] == "reject"            # changed mind after 50 days
    assert "Hola" in got["ORD-1005"]["reply"]               # replies in the buyer's language
    assert got["ORD-1002"]["risk"] == "high"                # $149 needs senior review
    assert all(c["status"] == "proposed" for c in got.values())


def test_approve_refunds_once_and_confirms_the_amount_only_afterwards():
    with TestClient(app) as client:
        client.post("/api/reset")
        for c in db.query("SELECT id FROM cases"):
            agent.analyze(c["id"])
        case = next(c for c in client.get("/api/cases").json() if c["order_id"] == "ORD-1001")
        assert "$" not in case["reply"]

        res = client.post("/api/cases/approve", json={"ids": [case["id"]]}).json()["results"][0]
        assert res["ok"] and res["refund_id"].startswith("DEMO-REF-")
        after = next(c for c in client.get("/api/cases").json() if c["id"] == case["id"])
        assert after["status"] == "refunded" and "$59.99" in after["final_message"] and after["refundable"] == 0

        again = client.post("/api/cases/approve", json={"ids": [case["id"]]}).json()["results"][0]
        assert not again["ok"]


def test_editing_cannot_break_the_guard_rails():
    with TestClient(app) as client:
        client.post("/api/reset")
        for c in db.query("SELECT id FROM cases"):
            agent.analyze(c["id"])
        case = next(c for c in client.get("/api/cases").json() if c["order_id"] == "ORD-1003")
        assert client.patch(f"/api/cases/{case['id']}", json={"amount": 500}).status_code == 422
        assert client.patch(f"/api/cases/{case['id']}", json={"reply": "We will refund $10."}).status_code == 422
        ok = client.patch(f"/api/cases/{case['id']}", json={"amount": 12.5})
        assert ok.status_code == 200 and ok.json()["edited"] == 1
