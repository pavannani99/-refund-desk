"""PayPal REST (sandbox). Without credentials every call is simulated and marked 'demo'."""
import time
import uuid

import httpx

from . import config

_token: dict = {"value": "", "expires": 0.0}


class PayPalError(Exception):
    pass


def _auth_header() -> dict:
    if time.time() > _token["expires"] - 60:
        r = httpx.post(f"{config.PAYPAL_BASE}/v1/oauth2/token",
                       auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
                       data={"grant_type": "client_credentials"}, timeout=20)
        if r.status_code != 200:
            raise PayPalError(f"PayPal auth failed ({r.status_code})")
        body = r.json()
        _token.update(value=body["access_token"], expires=time.time() + body.get("expires_in", 3000))
    return {"Authorization": f"Bearer {_token['value']}", "Content-Type": "application/json"}


def _call(method: str, path: str, json_body=None, request_id: str | None = None) -> dict:
    headers = _auth_header()
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # makes retries idempotent
    r = httpx.request(method, f"{config.PAYPAL_BASE}{path}", json=json_body, headers=headers, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json()
            msg = detail.get("details", [{}])[0].get("description") or detail.get("message") or r.text
        except ValueError:
            msg = r.text
        raise PayPalError(f"PayPal {r.status_code}: {msg}")
    return r.json() if r.content else {}


def create_captured_payment(order_id: str, item: str, amount: float, buyer_name: str) -> tuple[str, str]:
    """Create and capture a sandbox order paid with PayPal's published test card.

    Returns (capture_id, mode) where mode is 'sandbox' or 'demo'.
    """
    if not config.paypal_live():
        return f"DEMO-CAP-{uuid.uuid4().hex[:10].upper()}", "demo"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": order_id,
            "description": item[:127],
            "amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
        }],
        "payment_source": {"card": {
            "number": "4111111111111111", "expiry": "2030-12", "security_code": "123", "name": buyer_name,
        }},
    }
    order = _call("POST", "/v2/checkout/orders", body, request_id=f"seed-{order_id}-{uuid.uuid4().hex[:6]}")
    if order.get("status") != "COMPLETED":
        order = _call("POST", f"/v2/checkout/orders/{order['id']}/capture", {}, request_id=f"cap-{order['id']}")
    capture = order["purchase_units"][0]["payments"]["captures"][0]
    return capture["id"], "sandbox"


def get_capture(capture_id: str, mode: str) -> dict:
    if mode == "demo" or not config.paypal_live():
        return {"id": capture_id, "status": "COMPLETED", "source": "demo"}
    cap = _call("GET", f"/v2/payments/captures/{capture_id}")
    return {"id": cap["id"], "status": cap["status"], "amount": cap["amount"]["value"],
            "create_time": cap.get("create_time"), "source": "paypal_sandbox"}


def get_refund(refund_id: str, mode: str) -> dict:
    """Read the refund back from PayPal, so the reviewer sees PayPal's own record of it."""
    if mode == "demo" or not config.paypal_live() or refund_id.startswith("DEMO-"):
        return {"id": refund_id, "status": "COMPLETED", "source": "demo"}
    ref = _call("GET", f"/v2/payments/refunds/{refund_id}")
    return {"id": ref["id"], "status": ref["status"], "amount": ref["amount"]["value"],
            "currency": ref["amount"]["currency_code"], "create_time": ref.get("create_time"),
            "source": "paypal_sandbox"}


def refund_capture(capture_id: str, mode: str, amount: float, case_id: int, note: str) -> dict:
    if mode == "demo" or not config.paypal_live():
        return {"id": f"DEMO-REF-{uuid.uuid4().hex[:10].upper()}", "status": "COMPLETED", "source": "demo"}
    body = {"amount": {"currency_code": "USD", "value": f"{amount:.2f}"}, "note_to_payer": note[:255]}
    ref = _call("POST", f"/v2/payments/captures/{capture_id}/refund", body,
                request_id=f"refund-case-{case_id}-{amount:.2f}")
    return {"id": ref["id"], "status": ref["status"], "source": "paypal_sandbox"}
