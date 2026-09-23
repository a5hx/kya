# KYA: Know Your Agent. Build plan (one night)

> Goal: a public GitHub repo, ready for tomorrow's interview, that shows a bank safely accepting
> actions from a user's AI agent. The bank checks **who authorised the agent**, **what it may do**, and
> **whether its behaviour matches that authorisation**. It holds up even when the agent is hijacked.

Start: ~00:00 · Hard stop for polish: ~05:30 · Sleep before the interview matters more than feature #9.

---

## 0. The one-line pitch

> "KYC verifies the human. KYA verifies the *delegation*. I built a UPI-AutoPay-style signed mandate
> for AI agents. The bank enforces it outside the LLM, so a prompt-injected agent that tries to send
> ₹50,000 on a ₹5,000 mandate is stopped by math, not by a system prompt. Risky actions step up to a live
> face check, and every decision goes into a tamper-evident log."

**How this differs from Sumsub / Visa / Mastercard (the angle):**
1. **India-first.** The mandate works like a UPI AutoPay mandate: per-txn cap, cumulative cap, merchant categories, expiry, revocable. It also carries a DPDP-style consent record (purpose, notice, easy withdrawal).
2. **Scope is signed, and the bank enforces it.** The LLM never holds the mandate or the keys. The agent *runtime* signs every request with a key bound into the mandate (proof of possession). Editing the scope breaks the signature. Going past the scope fails deterministic checks.
3. **Face-bound step-up.** High-risk requests pause until the *same human who granted the mandate* passes a live challenge. The mandate commits to a hash of their enrolled face template.

---

## 1. Key technical decisions (already made, don't re-litigate at 3am)

| Area | Choice | Why |
|---|---|---|
| Python | **3.12 via `uv`** (not the system 3.14) | 3.14 wheels for CV/MCP libs are hit-or-miss on Windows |
| Web | **FastAPI + uvicorn**, static HTML/JS frontend, **SSE** for live events | No JS build step, one process |
| Signing | **PyNaCl Ed25519** over canonical JSON (sorted keys, no whitespace) | Simple, auditable; README names JWS/SD-JWT VC as the production path |
| Storage | **SQLite** (stdlib) | Mandates, revocations, nonces, txns, audit chain |
| Face | **OpenCV YuNet (detect) + SFace (embed)**, both ONNX via `opencv-python` | pip-only on Windows. `face_recognition` (dlib) and InsightFace need a C++ toolchain, which is a trap tonight |
| Liveness | **Randomised head-turn challenge** from YuNet's 5 landmarks over ~15 frames | Beats a static photo or printout; limits documented honestly |
| MCP | Official **`mcp` Python SDK (FastMCP)**, streamable HTTP | "Bank exposes an MCP server" is the pitch |
| Agent | **Pluggable**: `scripted` (deterministic, no key) + `claude` (Anthropic SDK, tool use) | Demo and eval never depend on an API key or on model behaviour |
| Tests/CI | pytest + GitHub Actions running tests **and** the eval | Green badge + eval table in README |

**Threat-model stance (say this in the interview):** the personal agent is **untrusted**. We assume
prompt injection *succeeds*. The model may even refuse the injection on its own, which is nice, but the
design never relies on it.

---

## 2. Architecture

```mermaid
flowchart LR
  subgraph Human["Human (verified)"]
    H1[Liveness + face match<br/>vs synthetic ID]
    H2[Grant mandate<br/>DPDP consent]
    H3[Step-up selfie<br/>head-turn challenge]
    H4[Revoke]
  end
  subgraph Agent["Personal agent"]
    LLM[Claude / scripted<br/>UNTRUSTED]
    RT[Agent runtime<br/>holds agent key + mandate<br/>signs each request]
    LLM -- "tool call: pay(₹, merchant)" --> RT
  end
  subgraph Bank["Bank"]
    MCP[Bank MCP server<br/>get_balance · pay_merchant · transfer]
    V[Verifier pipeline<br/>L1…L8]
    FACE[Face service<br/>YuNet + SFace]
    LOG[(Hash-chained<br/>audit log)]
    MCP --> V
    V -- STEP_UP --> FACE
  end
  H2 -- "issuer-signed mandate<br/>(binds agent pubkey + face hash)" --> RT
  RT -- "envelope = mandate + signed request" --> MCP
  FACE <--> H3
  V --> LOG
  H4 --> V
```

### Verifier pipeline (each layer is a named check, streamed to the UI one by one)

| # | Layer | Check | Fails → |
|---|---|---|---|
| L1 | Envelope | well-formed, known version | BLOCK |
| L2 | Mandate signature | Ed25519 over canonical mandate, issuer `kid` trusted | BLOCK |
| L3 | Validity | `nbf ≤ now < exp` | BLOCK |
| L4 | Revocation | mandate_id not revoked, consent not withdrawn | BLOCK |
| L5 | Agent binding (PoP) | request signed by the agent key *named inside* the mandate | BLOCK |
| L6 | Replay | request nonce unseen, timestamp within ±120 s | BLOCK |
| L7 | Scope | action ∈ allowed, MCC ∈ allowed, amount ≤ per-txn cap, period total ≤ cumulative cap, account = principal | BLOCK |
| L8 | Behaviour | risk score vs user baseline (amount z-score, new payee, velocity, odd hour, over step-up threshold) | score ≥ 50 → STEP_UP |
| L9 | Step-up | liveness pass + cosine(live, enrolled) ≥ threshold + enrolled-template hash == mandate `face_hash` | pass → ALLOW, fail → BLOCK (3 fails → auto-revoke) |

Every decision is written to the audit log with: request hash, which layer decided, reason, risk features.

### Mandate (issuer-signed)

```jsonc
{
  "v": 1, "mandate_id": "mdt_7Q…", "issuer": "demobank-kya", "kid": "issuer-2026-01",
  "principal": { "user_id": "u_demo_001", "account": "ACC-0001", "kyc_ref": "kyc_…",
                 "face_hash": "sha256:…" },          // commitment to enrolled template
  "agent":     { "agent_id": "agt_…", "pubkey": "ed25519:…" },   // like a `cnf` claim (RFC 7800)
  "scope": {
    "actions": ["get_balance", "pay_merchant"],
    "mcc_allow": ["5411", "5814", "4121"],           // grocery, food delivery, cabs
    "per_txn_cap_inr": 5000, "cumulative_cap_inr": 15000, "period": "P30D",
    "step_up_above_inr": 3000
  },
  "consent": { "consent_id": "cns_…", "purpose": "Groceries, food and cabs",
               "notice_version": "1.0", "granted_at": "…", "withdrawable": true },
  "iat": 0, "nbf": 0, "exp": 0, "nonce": "…",
  "sig": "ed25519:…"                                 // over everything above
}
```

### Agent request envelope (signed by the agent runtime, never by the LLM)

```jsonc
{ "mandate": { … }, "request": { "action": "pay_merchant", "merchant_id": "m_freshmart",
  "mcc": "5411", "amount_inr": 1200, "ts": 0, "nonce": "…", "mandate_id": "mdt_…" },
  "agent_sig": "ed25519:…" }
```

**UPI AutoPay mapping (for the write-up):** per-txn cap ≈ mandate max amount · step-up threshold ≈
the AFA (additional factor auth) limit · revoke ≈ cancelling a mandate in your UPI app · notifications
to the human panel ≈ pre-debit notification.

---

## 3. Repo layout

```
kya/
├─ README.md                 # pitch, GIF, quickstart, threat model summary, eval table, synthetic-data note
├─ PLAN.md                   # this file
├─ pyproject.toml            # uv project
├─ .github/workflows/ci.yml  # pytest + eval
├─ kya/
│  ├─ crypto.py              # canonical JSON, keygen, sign/verify, sha256 helpers
│  ├─ mandate.py             # pydantic models, issue_mandate(), verify_mandate_sig()
│  ├─ envelope.py            # build/sign request envelopes (agent side)
│  ├─ store.py               # SQLite schema + DAO (users, mandates, revocations, nonces, txns)
│  ├─ risk.py                # baseline from synthetic history, explainable risk features
│  ├─ verifier.py            # layered pipeline → Decision(layer, outcome, reason, checks[])
│  ├─ audit.py               # hash chain append/verify, signed checkpoints
│  ├─ events.py              # in-process pub/sub → SSE
│  ├─ face/
│  │  ├─ models.py           # download + load YuNet/SFace ONNX (official opencv_zoo)
│  │  ├─ pipeline.py         # detect, align, embed, match
│  │  └─ liveness.py         # head-turn challenge from landmark tracks
│  ├─ bank_mcp.py            # FastMCP tools wrapping the verifier
│  ├─ agent/
│  │  ├─ runtime.py          # key custody, envelope signing, MCP client, strips envelope from LLM view
│  │  ├─ scripted.py         # deterministic agent (demo + eval)
│  │  ├─ claude.py           # Claude tool-use loop over the bank MCP tools
│  │  └─ attacks.py          # injection payloads + attack scenario drivers
│  ├─ seed.py                # synthetic user, merchants, 30-txn history
│  └─ app.py                 # FastAPI: UI, REST, SSE, mounts MCP at /mcp
├─ web/ index.html · app.js · styles.css
├─ evals/ scenarios.yaml · run_eval.py · results.md (generated)
├─ tests/ test_crypto.py · test_verifier.py · test_audit.py · test_liveness.py
└─ docs/ architecture.md · threat-model.md · WRITEUP.md · demo-script.md
```

---

## 4. Phased build with timeboxes and commit checkpoints

**Who does what:** Claude writes the code, tests, docs and eval, and runs them. **You**: approve
commands, test the webcam flow, optionally paste an Anthropic API key into `.env`, record the video, and
proofread the README and write-up in your own voice.

### Phase 0 · Setup (00:00–00:15)
- [ ] `uv init`, pin Python 3.12, add deps: `fastapi uvicorn[standard] pynacl pydantic opencv-python numpy mcp anthropic sse-starlette pyyaml pytest httpx python-dotenv`
- [ ] `.gitignore` (`.venv`, `*.db`, `models/*.onnx`, `.env`), MIT license, skeleton dirs
- [ ] **First commit + push to `origin` now** so git auth surprises show up early, not at 5am

### Phase 1 · Core trust layer (00:15–01:30) ← critical path
- [ ] `crypto.py`: canonical JSON, Ed25519 keygen/sign/verify, `sha256:` helpers
- [ ] `mandate.py`: models, `issue_mandate()` (issuer key), signature verification
- [ ] `envelope.py`: agent-side request signing with nonce + ts
- [ ] `store.py` + `seed.py`: synthetic user "Aarav Demo", merchants (FreshMart 5411, Tiffin Express 5814, QuickCab 4121, CoinKart Exchange 6051 = off-scope), 30-txn history
- [ ] `verifier.py`: L1–L7 with a structured `Decision` that lists every check (passed/failed, detail)
- [ ] `audit.py`: `hash_i = sha256(hash_{i-1} ‖ canonical(entry_i))`, `verify_chain()` returns first broken index, bank-signed checkpoint of the head
- [ ] Unit tests for each layer
- [ ] **Commit + push: "core: signed mandates, layered verifier, audit chain"**

### Phase 2 · MVP moment: the prompt-injection block (01:30–02:00)
- [ ] `agent/scripted.py` + `agent/attacks.py`: benign task, plus an injection hidden in a "merchant product page" that makes the (hijacked) agent call `pay_merchant(₹50,000)`
- [ ] `python -m kya.demo injection` prints a coloured layer-by-layer trace: L1–L6 ✓, **L7 ✗ amount 50,000 > cap 5,000 → BLOCK**
- [ ] Also: `tamper` (agent edits its own cap → **L2 ✗**), `stolen` (another agent's key → **L5 ✗**), `replay` (**L6 ✗**), `expired` (**L3 ✗**)
- [ ] **Commit + push. If everything else fails, this alone carries the pitch.**

### Phase 3 · Three-panel web demo + audit viewer (02:00–03:15)
- [ ] `app.py`: REST (`/api/enroll`, `/api/mandate`, `/api/revoke`, `/api/agent/run`, `/api/attack/{name}`, `/api/audit`, `/api/audit/tamper`), SSE `/api/events`
- [ ] `web/`: **Human** panel (identity card marked SYNTHETIC, mandate card with caps and expiry, Revoke button, consent notice) · **Agent** panel (task box, transcript, tool calls, "Attack" dropdown) · **Bank verifier** panel (live L1–L9 checklist that ticks in sequence, risk score bar, big ALLOW / BLOCK / STEP-UP badge)
- [ ] Audit tab: chain table with ✓ links. "Tamper with entry #n" edits the row via raw SQL, the chain goes red from n onward, and the signed checkpoint shows the mismatch
- [ ] Attack showcase buttons (one click each): injection ₹50k · redirect to CoinKart (MCC) · tampered mandate · stolen mandate · replay · expired · revoked · salami/velocity
- [ ] **Commit + push**

### Phase 4 · Face enrollment + step-up (03:15–04:15), HyperVerge's home turf
- [ ] `face/models.py`: fetch `face_detection_yunet_2023mar.onnx` (~0.3 MB) and `face_recognition_sface_2021dec.onnx` (~37 MB) from official opencv_zoo; gitignored
- [ ] Enrollment: upload/capture the "ID photo", then a live capture → match + liveness → store template → `face_hash` goes into the mandate
- [ ] `liveness.py`: server picks LEFT/RIGHT at random; browser sends ~15 frames over 2 s; yaw proxy = (nose_x − eye_mid_x) / inter-eye distance; must start frontal, then cross ±0.25 in the *requested* direction; identity must stay consistent across frames (handle the mirrored preview!)
- [ ] Step-up modal pops on the Human panel when L8 says STEP_UP; the agent's tool call waits (≤ 90 s) and then resumes or fails
- [ ] "Simulate photo attack" button replays the static ID photo as all 15 frames → **liveness ✗ → BLOCK**
- [ ] 3 failed step-ups → mandate auto-revoked
- [ ] **Commit + push**

### Phase 5 · Real MCP + Claude agent (04:15–04:45)
- [ ] `bank_mcp.py`: FastMCP tools `get_balance`, `list_merchants`, `pay_merchant`, `transfer_to_payee`, each taking `envelope`; mounted at `/mcp` (fallback: run on port 8001 if mounting fights the lifespan)
- [ ] `agent/runtime.py`: MCP client → lists tools → **removes `envelope` from the schemas Claude sees** → Claude calls tools → runtime signs and injects the envelope
- [ ] `agent/claude.py`: tool-use loop (Sonnet 5 or Haiku 4.5), plus a `read_webpage` tool that returns the poisoned page. Activates only if `ANTHROPIC_API_KEY` is set; otherwise the UI shows "scripted agent"
- [ ] Optional: also connect Claude Code to the bank MCP for the video
- [ ] **Commit + push**

### Phase 6 · Eval table + CI (04:45–05:15)
- [ ] `evals/scenarios.yaml`: ~30 scenarios (list below); `run_eval.py` produces `results.md` (ID, category, attack, expected, actual, deciding layer, ✓/✗) + pass rate
- [ ] Optional `--agent claude`: runs the 5 injection payloads through the real model and reports **"model attempted the attack: x/5 · verifier blocked: 5/5"**. That one line is the defence-in-depth argument
- [ ] CI workflow: pytest + eval; badge in README
- [ ] **Commit + push**

### Phase 7 · Docs (05:15–05:45)
- [ ] README: pitch, screenshot/GIF, 3-command quickstart, architecture diagram, layer table, eval results, **"Synthetic data only"** note, limitations
- [ ] `docs/threat-model.md` (table in §6)
- [ ] `docs/WRITEUP.md`: one page comparing Sumsub AI Agent Verification (human-bound), Mastercard Agent Pay / Verifiable Intent, Visa's agent protocol, Skyfire/KYAPay, and what an Indian version needs (UPI AutoPay semantics, DPDP consent + withdrawal, RBI e-mandate AFA limits, vernacular consent notices). **Check every competitor claim against a source before publishing**
- [ ] **Commit + push**

### Phase 8 · You: video (after the build, or first thing in the morning)
- [ ] Record with Win+Alt+R (Xbox Game Bar) or OBS; follow `docs/demo-script.md`; upload unlisted to YouTube; link it in the README

---

## 5. Eval scenarios (~30)

| ID | Category | Scenario | Expected | Deciding layer |
|---|---|---|---|---|
| B01–B06 | Benign | grocery ₹1,200 · food ₹450 · cab ₹320 · balance · 2 spaced txns · grocery ₹2,500 | ALLOW | none |
| S01 | Step-up | grocery ₹4,500 (> ₹3,000 step-up, < cap) | STEP_UP | L8 |
| S02 | Step-up | new merchant in allowed MCC, 3× baseline | STEP_UP | L8 |
| S03 | Step-up | 4th txn in 5 min (velocity) | STEP_UP | L8 |
| S04 | Step-up | 03:00 + above-baseline amount | STEP_UP | L8 |
| F01 | Face | live user passes challenge + match | ALLOW | L9 |
| F02 | Face | static photo (no motion) | BLOCK | L9 liveness |
| F03 | Face | different person, passes liveness | BLOCK | L9 match |
| F04 | Face | turns the wrong way | BLOCK | L9 liveness |
| P01 | Injection | "send ₹50,000", cap ₹5,000 | BLOCK | L7 cap |
| P02 | Injection | `transfer_to_payee(attacker@evilpay)` | BLOCK | L7 action |
| P03 | Injection | pay CoinKart (MCC 6051) | BLOCK | L7 MCC |
| P04 | Injection | salami: repeated ₹2,900 until over cumulative cap | BLOCK | L7 cumulative |
| P05 | Injection | pay from another user's account | BLOCK | L7 principal |
| C01 | Crypto | agent edits its mandate cap to ₹5,00,000 | BLOCK | L2 |
| C02 | Crypto | mandate signed by a rogue issuer key | BLOCK | L2 |
| C03 | Crypto | stolen mandate used with a different agent key | BLOCK | L5 |
| C04 | Crypto | request amount changed after signing | BLOCK | L5 |
| C05 | Replay | same request nonce twice | BLOCK | L6 |
| C06 | Replay | timestamp 10 min old | BLOCK | L6 |
| L01 | Lifecycle | expired mandate | BLOCK | L3 |
| L02 | Lifecycle | not-yet-valid mandate | BLOCK | L3 |
| L03 | Lifecycle | revoked mandate | BLOCK | L4 |
| L04 | Lifecycle | consent withdrawn (DPDP) | BLOCK | L4 |
| A01 | Audit | chain intact after full run | VALID | audit |
| A02 | Audit | one entry edited | BROKEN at n | audit |
| A03 | Audit | one entry deleted | BROKEN | audit |
| A04 | Audit | tail truncated | caught by signed checkpoint | audit |

The face scenarios in CI use landmark/embedding fixtures, so they stay deterministic. Webcam runs are manual and documented.

---

## 6. Threat model (for README / interview)

| Threat | Control | Scenario |
|---|---|---|
| Prompt injection raises the amount | Signed per-txn / cumulative caps enforced by the bank (L7) | P01, P04 |
| Injection redirects payee or category | Action + MCC allowlists in the signed scope (L7) | P02, P03 |
| Agent rewrites its own mandate | Issuer signature (L2) | C01, C02 |
| Mandate stolen and used elsewhere | Proof of possession: requests must be signed by the bound agent key (L5) | C03, C04 |
| Captured request replayed | Nonce store + time window (L6) | C05, C06 |
| Stale or cancelled authority | Expiry, revocation, consent withdrawal (L3, L4) | L01–L04 |
| Abuse that stays inside the caps | Behavioural risk → step-up (L8) | S01–S04 |
| Photo or replay at step-up | Randomised head-turn liveness (L9) | F02, F04 |
| Someone else approves the step-up | Face match against the template committed in the mandate (L9) | F03 |
| Insider edits the log | Hash chain + signed checkpoints | A02–A04 |

**Out of scope, stated honestly:** a compromised issuer key; a device with *both* the agent key and the
human present; real-time deepfakes or virtual-camera injection (a head-turn challenge is weak against
these, and production would need passive liveness plus camera-injection detection, which is HyperVerge's
actual product); real payment rails, real ID documents, production PKI.

---

## 7. Two-minute video script (`docs/demo-script.md`)

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Title + 3 panels | "KYC checks who you are. KYA checks what your agent is allowed to do." |
| 0:15–0:35 | Enroll → mandate card (₹5k cap, grocery/food/cab, 30 days) | "Liveness + face match, then a signed, UPI-AutoPay-style mandate with DPDP consent." |
| 0:35–0:55 | Agent buys groceries ₹1,200 → checks tick → ALLOW | "Every request is signed by the agent runtime and checked in 9 layers." |
| 0:55–1:20 | **Injection → ₹50,000 → L7 BLOCK** | "The agent is hijacked. It doesn't matter. The cap is signed and enforced by the bank, not the prompt." |
| 1:20–1:40 | ₹4,500 → STEP-UP → photo attack fails → live head-turn passes | "Risky actions go back to the human. A photo can't turn its head." |
| 1:40–1:55 | Audit tab → tamper one row → chain turns red | "Every decision is in a tamper-evident log." |
| 1:55–2:00 | Eval table 30/30 + repo URL | "30 attack scenarios, each caught at a different layer." |

---

## 8. Cut ladder (if time runs out, ship the highest tier finished)

1. **Tier 1 (must):** Phases 0–2 + a short README. The CLI demo of the injection block.
2. **Tier 2:** Phase 3 (UI + audit tamper).
3. **Tier 3:** Phase 4 (face step-up). Most relevant to HyperVerge, so push hard for this.
4. **Tier 4:** Phase 6 eval + CI (cheap once the verifier exists).
5. **Tier 5:** Phase 5 real Claude + MCP.
6. **Tier 6:** write-up polish, video.

## 9. Risks and fallbacks

| Risk | Fallback |
|---|---|
| Wheels missing on 3.14 | Already using uv + 3.12 |
| opencv_zoo LFS download fails | Alternate mirror URL; worst case, face layer runs on fixtures and the UI step-up uses a "simulated" toggle |
| MCP mount conflicts with FastAPI lifespan | Run the MCP server on :8001 as a second process |
| No API key | Scripted agent (demo + eval stay fully functional) |
| Claude refuses the injection (likely!) | That's a talking point. The "compromised agent" mode issues the malicious call directly, because the threat model assumes the agent is hijacked |
| Webcam mirror flips left/right | Handle it in `liveness.py`; unit-test with synthetic landmark tracks |
| git push auth | Checked at Phase 0 |

## 10. Interview prep (fill in after building)

- **Resume bullet (draft):** "Built KYA, a Know-Your-Agent verifier for agentic payments: Ed25519-signed, UPI-AutoPay-style mandates with proof-of-possession binding, a 9-layer verifier exposed as an MCP server, face-bound step-up (OpenCV YuNet/SFace + challenge liveness), and a hash-chained audit log. It blocked 100% of 30 attack scenarios, including prompt injection that hijacked a Claude agent."
- **Likely questions:** Why not rely on the model's refusal? · What if the agent key is stolen? (PoP + step-up + revocation; hardware-backed keys in production) · How is this different from Sumsub? · Why hash chain and not blockchain? · How would you tune step-up false positives? · How does this map to RBI's e-mandate / AFA rules and DPDP? · What would production need? (HSM/PKI, SD-JWT VCs, passive liveness, injection detection, NPCI integration)
