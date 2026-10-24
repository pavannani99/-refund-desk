import json
import os

os.environ["DB_PATH"] = ":memory:"
os.environ["PAYPAL_CLIENT_ID"] = ""
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import agent, config, db, main, policy, seed, snapshot  # noqa: E402
from app.main import app  # noqa: E402

ORDER = {"amount": 50.0}


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """Each test starts from seeded samples, analyzed by the rules engine, with no saved snapshot."""
    monkeypatch.setattr(snapshot, "PATH", tmp_path / "none.json")
    main._last_reset["t"] = 0
    main._live_ai.clear()
    seed.seed()
    for c in db.query("SELECT id FROM cases"):
        agent.analyze(c["id"])


def by_order(client, order_id):
    return next(c for c in client.get("/api/cases").json() if c["order_id"] == order_id)


def proposal(**kw):
    base = {"action": "refund_full", "amount": 50.0, "confidence": 0.9, "sentiment": "calm",
            "reply": "Thanks for letting us know. Our team is reviewing your request and will follow up."}
    return {**base, **kw}


# --- guard rails ---

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


# --- agent ---

def test_rules_agent_follows_policy_on_the_samples():
    got = {c["order_id"]: c for c in db.query(db.CASE_SELECT)}
    assert got["ORD-1001"]["action"] == "refund_full"       # dead on arrival
    assert got["ORD-1002"]["action"] == "refund_full"       # not delivered, chargeback threat
    assert got["ORD-1003"]["action"] == "refund_partial"    # cosmetic scratch
    assert got["ORD-1004"]["action"] == "reject"            # changed mind after 50 days
    assert "Hola" in got["ORD-1005"]["reply"]               # replies in the buyer's language
    assert got["ORD-1002"]["risk"] == "high"                # $149 needs senior review
    assert all(c["status"] == "proposed" and c["ai_action"] == c["action"] for c in got.values())


def test_an_analysis_from_before_a_reset_is_not_written():
    case_id = db.one("SELECT id FROM cases WHERE order_id = 'ORD-1001'")["id"]
    db.update_case(case_id, status="queued", action=None)
    assert agent.analyze(case_id, still_current=lambda: False) is None
    assert db.case_view(case_id)["status"] == "queued"


def test_reviewer_corrections_are_fed_back_to_the_model():
    with TestClient(app) as client:
        case = by_order(client, "ORD-1003")
        client.patch(f"/api/cases/{case['id']}", json={"amount": 12.5})
    rows = agent.recent_corrections()
    assert len(rows) == 1 and rows[0]["ai_amount"] == 10.0 and rows[0]["amount"] == 12.5
    text = agent._corrections_prompt(rows)
    assert "you proposed refund_partial $10.00" in text and "changed it to refund_partial $12.50" in text


def test_undoing_an_edit_back_to_the_ai_draft_removes_the_correction():
    with TestClient(app) as client:
        case = by_order(client, "ORD-1003")
        client.patch(f"/api/cases/{case['id']}", json={"amount": 12.5})
        client.patch(f"/api/cases/{case['id']}", json={"amount": 10.0})
    assert agent.recent_corrections() == []


# --- API ---

def test_approve_refunds_once_records_who_decided_and_confirms_the_amount_only_afterwards():
    with TestClient(app) as client:
        case = by_order(client, "ORD-1001")
        assert "$" not in case["reply"]

        res = client.post("/api/cases/approve", json={"ids": [case["id"]]}).json()["results"][0]
        assert res["ok"] and res["refund_id"].startswith("DEMO-REF-")
        after = by_order(client, "ORD-1001")
        assert after["status"] == "refunded" and "$59.99" in after["final_message"] and after["refundable"] == 0
        assert after["decided_by"] == "Reviewer (AI draft as proposed)" and after["decided_at"]

        assert client.get(f"/api/cases/{case['id']}/refund-status").json()["status"] == "COMPLETED"
        again = client.post("/api/cases/approve", json={"ids": [case["id"]]}).json()["results"][0]
        assert not again["ok"]


def test_editing_cannot_break_the_guard_rails():
    with TestClient(app) as client:
        case = by_order(client, "ORD-1003")
        assert client.patch(f"/api/cases/{case['id']}", json={"amount": 500}).status_code == 422
        assert client.patch(f"/api/cases/{case['id']}", json={"reply": "We will refund $10."}).status_code == 422
        ok = client.patch(f"/api/cases/{case['id']}", json={"amount": 12.5})
        assert ok.status_code == 200 and ok.json()["edited"] == 1


def test_the_demo_loads_the_saved_ai_run_without_calling_the_ai(tmp_path, monkeypatch):
    saved = [{"order_id": "ORD-1001", "intent": "damaged", "sentiment": "calm", "confidence": 0.93,
              "action": "refund_full", "amount": 59.99, "reasoning": "Dead on arrival.", "risk": "low",
              "reply": "Sorry about that. Our team is reviewing your request.", "agent": "gemini:test",
              "trace": [{"tool": "get_payment", "result": {"paypal_capture": {"id": "OLD", "status": "COMPLETED"}}}]}]
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(saved))
    monkeypatch.setattr(snapshot, "PATH", path)
    monkeypatch.setattr(agent, "analyze", lambda *a, **k: pytest.fail("the AI must not run for saved cases"))
    seed.seed()
    assert snapshot.apply() == 1
    case = db.one(db.CASE_SELECT + " WHERE c.order_id = 'ORD-1001'")
    assert case["status"] == "proposed" and case["agent"] == "gemini:test (saved run)"
    assert json.loads(case["trace"])[0]["result"]["paypal_capture"]["id"] == case["capture_id"]


def test_the_public_demo_caps_live_ai_use(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "LIVE_AI_PER_HOUR", 2)
    monkeypatch.setattr(main, "_enqueue", lambda case_id: None)
    with TestClient(app) as client:
        body = {"order_id": "ORD-1001", "subject": "Hi", "message": "It crackles."}
        assert client.post("/api/cases", json=body).status_code == 200
        assert client.post("/api/cases", json=body).status_code == 200
        assert client.post("/api/cases", json=body).status_code == 429


def test_reset_is_rate_limited():
    with TestClient(app) as client:
        assert client.post("/api/reset").status_code == 200
        assert client.post("/api/reset").status_code == 429


def test_a_reply_in_another_language_than_the_buyer_wrote_is_refused():
    msg = "Never received my order. Tracking says the package is lost and I want my money back."
    fr = "Bonjour Chloe, je suis vraiment désolée. Notre équipe examine votre demande et nous vous répondrons."
    errors, _ = policy.check_proposal(proposal(reply=fr, amount=39.99), {"amount": 39.99}, 0, msg)
    assert any("buyer wrote in English" in e for e in errors)
    es_msg = "Hola, el micrófono no funciona, no se conecta con mi teléfono y la luz parpadea. Gracias."
    es = "Hola Sofía, lamento mucho las molestias. Nuestro equipo está revisando su solicitud y le responderá pronto."
    assert policy.check_proposal(proposal(reply=es, amount=39.99), {"amount": 39.99}, 0, es_msg)[0] == []
    assert policy.check_proposal(proposal(amount=39.99), {"amount": 39.99}, 0, "Help!!")[0] == []


CSV_HEAD = "buyer_name,item,amount,subject,message,order_id,channel\n"


def test_csv_import_creates_cases_and_skips_bad_rows(monkeypatch):
    queued = []
    monkeypatch.setattr(main, "_enqueue", queued.append)
    body = CSV_HEAD + ('Ann,Earbuds,79.00,Silent earbud,"The left one is silent, help.",,email\n'
                       "Bo,Stand,abc,Late,Never came,,\n"
                       "Cy,Headset,45,Broken,Cable broke,ORD-X1,paypal_dispute\n")
    with TestClient(app) as client:
        before = len(client.get("/api/cases").json())
        res = client.post("/api/import", json={"csv": body}).json()
        assert res["imported"] == 2 and res["errors"] == [{"row": 3, "error": "amount 'abc' is not a number"}]
        assert len(client.get("/api/cases").json()) == before + 2 and len(queued) == 2
    assert db.one("SELECT channel FROM cases WHERE order_id = 'ORD-X1'")["channel"] == "paypal_dispute"


def test_csv_import_reuses_an_existing_order(monkeypatch):
    monkeypatch.setattr(main, "_enqueue", lambda case_id: None)
    with TestClient(app) as client:
        client.post("/api/import", json={"csv": CSV_HEAD + "Ann,Earbuds,79,Again,Still silent,ORD-1001,email\n"})
    assert db.one("SELECT COUNT(*) AS n FROM orders WHERE id = 'ORD-1001'")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM cases WHERE order_id = 'ORD-1001'")["n"] == 2


def test_csv_import_rejects_missing_columns_and_too_many_rows():
    with TestClient(app) as client:
        assert client.post("/api/import", json={"csv": "buyer_name,item\nAnn,Earbuds\n"}).status_code == 422
        big = CSV_HEAD + "Ann,Earbuds,10,Hi,Hello,,\n" * 26
        assert client.post("/api/import", json={"csv": big}).status_code == 413


def test_the_sample_csv_imports_cleanly(monkeypatch):
    monkeypatch.setattr(main, "_enqueue", lambda case_id: None)
    sample = (main.STATIC / "sample-import.csv").read_text(encoding="utf-8")
    with TestClient(app) as client:
        res = client.post("/api/import", json={"csv": sample}).json()
    assert res == {"imported": 3, "errors": [], "waiting_for_ai": 0}
