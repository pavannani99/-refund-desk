# Devpost submission text

**Name:** Refund Desk

**Tagline:** An AI refund agent for small PayPal sellers. The AI decides, a person approves, PayPal pays.

## Inspiration
Every small online shop has the same inbox: "it arrived broken", "it never came", "I changed my mind", and PayPal disputes. Each one takes 5 to 10 minutes: find the order, check the payment, check the policy, decide, reply, refund. AI can do most of that, but a support bot that moves money on its own is risky. One confident reply like "we'll refund 50%" commits the seller before anyone has agreed. We wanted the AI's speed with a person's control over the money.

## What it does
- A **Gemini agent** reads each buyer message or PayPal dispute. It calls tools to look up the order and the **live PayPal capture** and the buyer's history, then proposes a full refund, a partial refund, a rejection or a request for more information. Each proposal comes with a confidence score, its reasons and a reply in the buyer's language.
- **Guard rails in code** check every proposal:
  - no refund above what's still refundable;
  - a hard ceiling, with large refunds flagged;
  - the reply can't promise money before approval;
  - rule breaks go back to the AI to fix.
- An **AG Grid review desk** lets a person:
  - filter (high risk, PayPal disputes, AI unsure) and see how long each buyer has been waiting;
  - edit the action, amount or reply inline, with the same rules and undo;
  - approve one case or many, after a confirmation that shows the total.
- **PayPal refunds the capture** through the Payments API, with idempotency keys. The buyer is told the amount only after PayPal confirms. The reviewer can read the refund back from PayPal.
- **The AI learns from the reviewer:** corrections to its drafts are fed into its next decisions.
- An **audit trail** records who decided each case and when, and whether the AI's draft was used as-is or changed.

## How we built it
- **PayPal (sandbox):** OAuth, Orders v2 to create and capture payments, Payments v2 to read captures, refund them and read refunds back. Every call carries a `PayPal-Request-Id`.
- **AI:** Google Gemini with function calling (free tier), in a tool loop:
  - `get_payment`, `get_buyer_history`, `propose_resolution`;
  - code-side validation that feeds errors back to the model;
  - model fallback when a free daily quota runs out;
  - a rules engine so the app still works without a key.
- **AG Grid Community and AG Charts Community** for the review desk. We use checkbox selection, per-row actions, validated inline editors, undo/redo, filter models, an external filter, value getters, cell flash, a pinned totals row, pagination, overlays, saved grid state, CSV export, and light and dark themes.
- **FastAPI and SQLite**, hosted on **Render**. One worker queue keeps the AI inside free-tier limits. The hosted demo loads a saved run of the real agent, so it starts instantly.

## Challenges
- Making the AI safe to put next to money. Prompts alone weren't enough, so every rule is enforced in code, and the AI must fix its own rule breaks.
- Free-tier limits: retries, per-minute versus per-day quota handling, model fallback, and a saved run for the public demo.
- Sandbox regions: sandbox accounts from some countries can't take card payments, so we documented the US sandbox setup and kept a demo mode.

## What we learned
The interesting design work isn't the AI's answer. It's the boundary between what the AI may decide and what a person must approve, and making that boundary visible in the UI.

## What's next
- Pull disputes straight from the PayPal Disputes API and propose evidence.
- PayPal webhooks for refund status.
- Per-shop policy editing.
- Shopify and marketplace inbox connectors.

## Built with
PayPal REST APIs (Orders v2, Payments v2), Google Gemini, AG Grid, AG Charts, Python, FastAPI, SQLite, Render.

## Prizes we're entering
Best Use of AG Grid. Best Use of PayPal + AI.
