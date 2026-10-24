# Demo video script (target 2:45)

Record at 1440×900 or larger, light mode, browser zoom 100%. Before recording:
- click **Reset demo**;
- make sure the header says `PayPal: Sandbox · 12 live payment(s)` and `AI: gemini:…`;
- open a second tab on the PayPal sandbox business account's **Activity** page.

| Time | On screen | Say (roughly) |
|---|---|---|
| 0:00–0:20 | Title card, then the full Refund Desk screen | "Small online sellers spend hours a week on refund requests and PayPal disputes: read the message, find the order, check the payment, check the policy, decide, reply, refund. Refund Desk gives that work to an AI agent, but a person still approves every dollar." |
| 0:20–0:45 | Click case #2 (James, PayPal dispute, not received). Scroll the panel to **What the AI did** | "Each message goes to a Gemini agent with tools. Here it looked up the live PayPal capture, saw the order is still in transit after 16 days, checked the buyer's history, and proposed a full refund. Because it's $149, it's flagged high risk. Notice the reply: it doesn't promise anything." |
| 0:45–1:05 | Open the **Guard rails** bar | "These rules are checked in code, not just asked of the AI. A refund can't exceed what's left on the capture. Large ones are flagged. And the reply can't promise money before a person approves. If the AI breaks a rule, the proposal goes back to it with the reason." |
| 1:05–1:30 | Double-click **Refund** on #3 and type 500: refused. Type 12.50: saved, cell flashes. Double-click **Reply**, type "We will refund you $10": refused | "The review desk is built on AG Grid. I can edit any decision inline, and the same guard rails apply to me. This correction is saved, and the AI uses it on the next similar case." |
| 1:30–1:55 | Click the **High risk** chip, then **Everything**. Tick 4 cases and click **Approve 4**. Show the confirmation (total, high-risk warning), then approve | "I can filter by risk, disputes or where the AI was unsure, then approve in bulk. The confirmation shows exactly what will be paid." |
| 1:55–2:15 | Toast "PayPal refund(s) issued". Switch to the PayPal sandbox tab and refresh: the refunds appear. Back in the app, open the refunded case and click **Check status at PayPal** | "Approving refunds the real PayPal capture through the Payments API, with an idempotency key so it can never pay twice. Only now is the buyer told the amount." |
| 2:15–2:35 | **+ New message**: type a Spanish complaint and send. The row appears as "AI working…", then fills in | "New messages are analyzed live. It replies in the buyer's language." |
| 2:35–2:45 | KPIs and charts | "Faster replies for buyers, hours back for sellers, and no refund leaves without a person saying yes. That's Refund Desk: built with PayPal, Gemini and AG Grid." |

Tips:
- Keep the mouse slow.
- Cut any waiting in editing.
- No background music unless it's licensed (the rules forbid unlicensed material).
