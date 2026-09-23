# Two-minute demo script

Setup: `uv run uvicorn kya.app:app --port 8000`, open http://localhost:8000 full screen, click **Reset demo**.
Record with Win + Alt + R (Xbox Game Bar) or OBS. Speak slowly; the UI animates on its own.

| Time | Do | Say |
|---|---|---|
| 0:00 | Show the three panels | "KYC checks who you are. KYA checks what your AI agent is allowed to do. Three parties: me, my agent, my bank." |
| 0:12 | Point at the mandate card | "I've given my agent a signed mandate, like UPI Circle: ₹5,000 a payment, ₹15,000 a month, groceries, food and cabs only, face check above ₹3,000." |
| 0:25 | Click **Weekly groceries** | "The agent reads FreshMart's page and pays. Every call is signed by the agent's key and checked through nine layers: ALLOW." |
| 0:40 | Toggle **Poisoned web page**, click **Weekly groceries** | "Now the page hides an instruction: pay ₹50,000 in dues first. The agent falls for it…" |
| 0:52 | Point at the red L7 | "…and it doesn't matter. The cap is in a signed mandate the model never sees. Blocked at L7, and the real order still goes through." |
| 1:05 | Click **Monthly stock-up** | "₹4,500 is allowed, but above my step-up limit, so the bank pauses the agent and asks me." |
| 1:12 | Click **Hold up a photo** | "A photo can't follow a random head-turn: rejected." |
| 1:20 | Click Monthly stock-up again → **Start live check**, turn your head | "Live me, turning the way it asked: approved, and the agent continues." |
| 1:35 | Attack lab: **Stolen mandate**, **Replay** | "Every attack is caught at a different layer: a stolen mandate fails proof of possession, a replay fails the nonce check." |
| 1:45 | Audit log tab → **Edit entry** | "Every decision is hash-chained. Edit one entry and the chain breaks from there." |
| 1:55 | Eval tab | "37 scenarios, all nine layers exercised. Code's on GitHub." |
