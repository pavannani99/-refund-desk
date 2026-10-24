import os

from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Tried in order; when one model's free daily quota runs out the agent moves to the next.
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-3.5-flash,gemini-3.8-flash,gemini-3.1-flash-lite").split(",") if m.strip()]
# Free-tier friendly pause between Gemini calls, in seconds.
GEMINI_PAUSE = float(os.getenv("GEMINI_PAUSE", "1.5"))
# A public demo must not burn the free AI quota: new buyer messages analyzed live, per rolling hour.
LIVE_AI_PER_HOUR = int(os.getenv("LIVE_AI_PER_HOUR", "10"))

DB_PATH = os.getenv("DB_PATH", "refund_desk.db")

# Refund guard rails. A person approves every refund; these are hard limits on what the AI may propose.
REFUND_WINDOW_DAYS = int(os.getenv("REFUND_WINDOW_DAYS", "30"))
SENIOR_REVIEW_USD = float(os.getenv("SENIOR_REVIEW_USD", "100"))  # proposals above this are flagged
HARD_CEILING_USD = float(os.getenv("HARD_CEILING_USD", "500"))    # proposals above this are refused


def paypal_live() -> bool:
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def gemini_live() -> bool:
    return bool(GEMINI_API_KEY)
