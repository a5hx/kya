# KYA: Know Your Agent

[![ci](https://github.com/a5hx/kya/actions/workflows/ci.yml/badge.svg)](https://github.com/a5hx/kya/actions/workflows/ci.yml)

**KYC verifies the human. KYA verifies the delegation.** This is a working prototype of how a bank can accept
payments from a customer's AI agent. The bank checks **who authorised the agent**, **what it is allowed to do** and
**whether this request matches that authority**, and the checks still hold when the agent has been hijacked by prompt
injection.

> A hijacked agent that tries to send ₹50,000 on a ₹5,000 mandate is stopped by a signature and a deterministic
> rule, not by a system prompt.

**Synthetic data only.** Every identity, account, merchant and transaction here is invented. The only biometric the
demo ever touches is the operator's own webcam face, processed locally. No real ID documents, payment rails or
customer data are used.

![Prompt injection blocked: the hijacked agent tries to pay ₹50,000, the bank stops it at L7, the real ₹1,149 order goes through](docs/screenshot.png)

<sub>The agent read a poisoned product page and tried to pay ₹50,000. Layers L1–L6 pass (it *is* the right agent with
a valid mandate), L7 blocks the amount, and the legitimate ₹1,149 order still goes through.</sub>

---

## Why

An agent can act on a fully verified account, with a valid payment token, and still do something its owner never
intended. KYC answers "who is this person?" and tokenisation answers "is this card real?". Neither answers
"was *this agent* allowed to do *this*, right now?".

India's payment network is heading the same way. NPCI is reportedly building a Unified Agent Protocol for AI-agent
payments on UPI, and at Global Fintech Fest 2026 its chairman said AI "may recommend", but authentication and final
settlement must follow deterministic, auditable rules. This repo builds that separation end to end.

## What it does

Three parties, each shown as a live panel in the demo:

| Party | Role | Trust |
|---|---|---|
| **Human** (Aarav, synthetic) | Passes liveness + face match, grants a mandate, can revoke it, approves risky payments with a live face check | Verified |
| **Personal agent** (Claude or a scripted stand-in) | Browses merchant pages and asks the bank to pay | **Untrusted.** Assumed prompt-injectable |
| **Bank verifier** | Checks every request through 9 layers, executes or blocks, logs every decision | Enforces |

```mermaid
flowchart LR
  subgraph H["Human"]
    K["Liveness + face match<br/>vs ID photo"]
    G["Grant mandate<br/>(DPDP-style consent)"]
    S["Step-up: live<br/>head-turn challenge"]
  end
  subgraph A["Personal agent (untrusted)"]
    LLM["Model: Claude / scripted"]
    RT["Runtime: holds agent key<br/>+ mandate, signs every call"]
    LLM -- "pay_merchant(m, ₹)" --> RT
  end
  subgraph B["Bank"]
    MCP["MCP server<br/>(streamable HTTP)"]
    V["Verifier L1–L9"]
    F["Face service<br/>YuNet + SFace"]
    LOG[("Hash-chained<br/>audit log")]
  end
  G -- "Ed25519-signed mandate<br/>binds agent key + face hash" --> RT
  RT -- "envelope = mandate + signed request" --> MCP --> V
  V -- "STEP_UP" --> F <--> S
  V --> LOG
```

### The mandate

Modelled on UPI's existing delegation primitives. The caps are UPI Circle's full-delegation limits (₹5,000 per
payment, ₹15,000 per month), the step-up plays the role of Circle's partial delegation (the primary approves), and
the lifecycle works like an AutoPay mandate (categories, validity window, revocable). It carries a DPDP-style consent
record. The bank signs it with Ed25519. It names the agent's public key, so only that agent can use it, and commits
to a hash of the human's enrolled face template.

```jsonc
{
  "mandate_id": "mdt_…", "issuer": "demobank-kya", "kid": "issuer-2026-01",
  "principal": { "user_id": "u_demo_001", "account": "ACC-0001", "face_hash": "sha256:…" },
  "agent":     { "agent_id": "agt_aarav_assistant", "pubkey": "ed25519:…" },   // proof of possession
  "scope": { "actions": ["get_balance", "list_merchants", "pay_merchant"],
             "mcc_allow": ["5411", "5814", "4121"],                            // grocery, food, cabs
             "per_txn_cap_inr": 5000, "cumulative_cap_inr": 15000, "period_days": 30,
             "step_up_above_inr": 3000 },
  "consent": { "purpose": "Groceries, food delivery and cabs", "withdrawable": true, … },
  "nbf": …, "exp": …, "nonce": "…", "sig": "ed25519:…"
}
```

Every tool call is wrapped by the **agent runtime** (not the model) as `{mandate, request, agent_sig}`. The request
carries a fresh nonce and timestamp. The model never sees the key or the mandate, so it cannot widen, forge or
re-sign its own authority.

### The verifier: 9 layers, each with a named reason

| # | Layer | Check | On failure |
|---|---|---|---|
| L1 | Envelope | Well-formed v1 envelope; MCP tool args must equal the signed request | BLOCK |
| L2 | Mandate signature | Ed25519 over canonical JSON, trusted issuer key id | BLOCK |
| L3 | Validity window | `nbf ≤ now < exp` | BLOCK |
| L4 | Revocation & consent | Not revoked; consent not withdrawn | BLOCK |
| L5 | Agent binding (PoP) | Request signed by the key *named in* the mandate | BLOCK |
| L6 | Replay | Unseen nonce, timestamp within ±120 s | BLOCK |
| L7 | Scope | Action allowed; merchant category (from the **bank's** registry) allowed; per-payment and 30-day caps; account = principal | BLOCK |
| L8 | Behaviour risk | Explainable score against the *human's* own history: step-up limit, unusual amount, new merchant, velocity, odd hour | score ≥ 50 → STEP-UP |
| L9 | Face step-up | Random head-turn liveness + face match against the template whose hash is in the mandate | pass → ALLOW, fail → BLOCK; 3 fails → auto-revoke |

Every decision (and every mandate, consent and biometric event) goes into a **hash-chained audit log** with a
bank-signed checkpoint of the head. Editing, re-hashing, deleting or truncating entries is detected.

![An insider rewrites a blocked ₹50,000 payment as an allowed ₹1 payment; the chain breaks from that entry onward](docs/audit.png)

## Results

**37/37 attack and benign scenarios pass, and together they exercise all 9 layers** ([full table](evals/results.md)).
Each attack is stopped at a different layer:

| Attack | Stopped at |
|---|---|
| Prompt injection: "pay ₹50,000" on a ₹5,000 cap | L7 cap |
| Injection redirects payment to a crypto exchange / P2P transfer to attacker | L7 category / action |
| Agent rewrites its own mandate; mandate forged with an attacker's key | L2 signature |
| Stolen mandate used by another agent; amount altered in transit | L5 proof of possession |
| Replayed request; request captured and replayed 10 min later | L6 nonce / window |
| Expired, not-yet-valid, revoked mandate; consent withdrawn | L3 / L4 |
| Salami: many small payments; 4 payments in 3 min; 3 a.m. spending | L7 cumulative cap / L8 step-up |
| Photo at step-up; wrong-way turn; impostor face; two faces; swapped template in DB | L9 |
| Insider edits, re-hashes, deletes or truncates the audit log | Hash chain + signed checkpoint |

Honest caveat: I wrote these scenarios alongside the implementation, so they are a regression and coverage suite,
not an independent benchmark. The independent part is `--claude` mode below.

**Against a real model.** `uv run python -m evals.run_eval --claude` sends 5 styles of injected page (hidden HTML,
fake system notice, fake fraud alert, poisoned review, split payments) to a Claude agent, in normal and
"compromised" modes, and records whether the model *attempted* the malicious action and whether the bank blocked
it. The design point: the verdict never depends on whether the model resisted.

## Run it

Needs [uv](https://docs.astral.sh/uv/). Windows, macOS or Linux.

```bash
uv sync
uv run python -m kya.demo all        # terminal: every attack, layer by layer
uv run uvicorn kya.app:app --port 8000
```

Open http://localhost:8000. The face models (OpenCV YuNet 0.2 MB + SFace 37 MB) download from the official
`opencv_zoo` on first camera use. Optional: put `ANTHROPIC_API_KEY=…` in `.env` to let Claude play the agent
(default model `claude-opus-5`, override with `KYA_MODEL`).

```bash
uv run pytest -q                      # 65 unit tests
uv run python -m evals.run_eval       # 37-scenario eval → evals/results.md
```

### Demo walkthrough

1. **Normal task.** Agent panel → *Weekly groceries*. Watch L1–L9 tick in the verifier panel: ALLOW.
2. **Prompt injection.** Turn on *Poisoned web page* and run *Weekly groceries* again. FreshMart's page hides an
   instruction to pay ₹50,000. The agent obeys; the bank blocks it at **L7** and the real ₹1,149 order still goes through.
3. **Face step-up.** *Monthly stock-up* (₹4,500) is inside the mandate but above the ₹3,000 step-up limit. The agent's
   call pauses while the Human panel asks for a live head-turn (random direction). *Hold up a photo* fails liveness.
4. **Attack lab.** One click per attack; each shows the layer that caught it.
5. **Revoke / withdraw consent.** The next agent call fails at L4. Withdrawal also erases the face template.
6. **Audit log tab.** Tamper with an entry and watch the chain break from that point.
7. **Verify me with camera.** Upload any photo of yourself as the "ID photo", pass the head-turn check, and the
   bank issues a new mandate bound to your face.

## Design choices

- **Scope is signed by the issuer and enforced by the bank, outside the model's trust boundary.** Prompt injection
  can change what the model *wants*, not what the bank *allows*.
- **Proof of possession** (like RFC 7800 `cnf` / DPoP): a stolen mandate is useless without the agent's private key.
- **The merchant category comes from the bank's registry**, never from the agent, so an agent cannot relabel a crypto
  exchange as a grocery store.
- **The risk baseline is the human's own history.** Agent payments are excluded, so a hijacked agent cannot drift
  its own baseline upward.
- **Explainable risk, not a black box.** Every point of the score maps to a sentence.
- **Hash chain plus signed checkpoints, not a blockchain.** One writer, and the tamper evidence comes from signing
  the head, not from consensus.
- **OpenCV YuNet + SFace**: pip-only on every OS (no dlib or C++ toolchain), CPU-fast, published thresholds.

## Threat model and limits

The full table is in [docs/threat-model.md](docs/threat-model.md). **Out of scope, stated plainly:**

- A compromised issuer key, or a device where the attacker holds **both** the agent key and the human.
- **Real-time deepfakes and virtual-camera injection.** A head-turn challenge stops photos and replays, not a live
  face-swap. Production needs passive liveness and camera-injection detection, which is exactly the problem
  identity-verification vendors specialise in.
- Real payment rails, NPCI integration, production PKI/HSM (keys here live in SQLite for the demo), and real ID documents.

**Production path:** mandates as SD-JWT VCs or W3C VCs; hardware-backed agent keys (Secure Enclave / StrongBox);
HSM-held issuer keys with rotation; checkpoints published to an external transparency log; passive liveness and
injection-attack detection; mapping the mandate onto whatever NPCI's agent protocol and registry standardise.

See [docs/WRITEUP.md](docs/WRITEUP.md) for how this compares with Sumsub, Mastercard, Visa and Skyfire, and what an
Indian version needs.

## Layout

```
kya/crypto.py        canonical JSON, Ed25519, hashing
kya/mandate.py       mandate schema + issuing
kya/envelope.py      agent-side request signing
kya/verifier.py      L1–L8 pipeline → Decision with per-layer checks
kya/risk.py          explainable behavioural score (L8)
kya/bank.py          execution, step-up (L9), audit logging
kya/audit.py         hash chain, signed checkpoints, tamper simulation
kya/bank_mcp.py      the bank's MCP server
kya/service.py       async front door; pauses calls for step-up
kya/agent/           wallet (key custody), runtime, scripted + Claude agents, attacks
kya/face/            YuNet/SFace pipeline, head-turn liveness, templates
kya/app.py + web/    three-panel demo (FastAPI + SSE, vanilla JS)
evals/run_eval.py    37 scenarios (+ optional Claude injection trials)
```

MIT licensed.
