# Refund Desk

**An AI refund agent for small PayPal sellers, with a person approving every payout.**

Small shops lose hours every week to "it arrived broken", "where is my order" and PayPal disputes. Fully automatic bots are risky: one wrong reply can promise a refund the seller never agreed to. Refund Desk splits the work:

1. **The AI does the reading and thinking.** For each buyer message or PayPal dispute, a Gemini agent:
   - looks up the order and the **live PayPal capture**;
   - checks the buyer's history and the store's refund policy;
   - proposes a full refund, a partial refund, a rejection or a request for more information, with a confidence score and its reasons;
   - drafts a reply in the buyer's own language.
2. **Code enforces the guard rails.** Every proposal is checked in code before anyone sees it. A refund can't exceed what's still refundable on the order. A hard ceiling applies. Large amounts are flagged for senior review. The draft reply may never promise money, an amount or a timeline. A proposal that breaks a rule goes back to the model to fix.
3. **A person approves in an AG Grid review desk.** Filter, sort and search the cases, edit the action, amount or reply inline (the same guard rails apply), then approve in bulk.
4. **PayPal pays.** Approving calls the PayPal Payments API to refund the capture. Retries can't double-pay, because each refund call carries a `PayPal-Request-Id`. The buyer's confirmation message is written only after the refund exists.

## Run it (2 minutes)

```bash
git clone <this repo> && cd refund-desk
python -m venv .venv
.venv/Scripts/activate            # Windows. On macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # optional: add keys, see below
uvicorn app.main:app --port 8765
```

Open http://localhost:8765. Twelve sample cases load and are analyzed automatically.

**With no keys it still runs fully**, in demo mode: PayPal calls are simulated, and a rules engine stands in for the AI.

Add free keys to `.env` for the real thing:

| Key | Where | Cost |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | [PayPal Developer → Apps & Credentials → Sandbox](https://developer.paypal.com/dashboard/applications/sandbox) | Free |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) | Free tier, no card |

> **Use a US sandbox business account.** The sample orders are paid with PayPal's sandbox test card, and sandbox business accounts from some regions (for example India) can't take card payments. In the PayPal Developer Dashboard go to **Testing Tools → Sandbox Accounts → Create account**, choose **Business** and **United States**, then **Apps & Credentials → Create App** (Merchant) linked to that account, and use its keys. If a payment can't be created, that order falls back to demo mode and the app keeps working.

With PayPal keys, the seed step creates and captures a real sandbox payment for every sample order, using PayPal's test card. Approving a refund in the grid then refunds that capture, and you can see it in your sandbox business account.

Tests: `pip install pytest && pytest`

## How it's built

| Part | What it does | File |
|---|---|---|
| PayPal | OAuth, Orders v2 (create and capture), Payments v2 (get capture, refund), idempotency keys | `app/paypal.py` |
| AI agent | Gemini function calling: `get_payment`, `get_buyer_history`, `propose_resolution`, in a loop of up to 6 turns | `app/agent.py` |
| Guard rails | Refund limits, no-promise reply check, risk rating | `app/policy.py` |
| Review desk | AG Grid Community: checkbox bulk selection, inline select, number and large-text editors with server-side validation, quick filter, external filter views, pinned totals row, row-risk styling, custom cell renderers, CSV export | `static/app.js` |
| API | FastAPI with SQLite; a single worker queue keeps the AI inside free-tier rate limits | `app/main.py` |

## Tools used

- **PayPal Developer Platform (sandbox):** Orders v2 and Payments v2 (captures and refunds).
- **Google Gemini 3.8 Flash:** the agent's reasoning, tool calls and multilingual replies.
- **AG Grid Community:** the human review desk.
- **Chart.js:** the summary charts.
- **Render:** hosting (`render.yaml`).

## License

MIT
