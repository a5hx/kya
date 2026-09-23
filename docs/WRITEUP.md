# Know Your Agent for Indian payments: what exists, and what's missing

*Ashwin Kumar · September 2026 · companion to the [KYA prototype](../README.md)*

## The problem in one sentence

Once an AI agent can pay, "is this person verified?" stops being the interesting question. What matters is
**whether this agent was authorised to make this payment, and whether it is still behaving like that
authorisation**.

## Who is solving which part

| Approach | Core question it answers | Mechanism | Where it sits |
|---|---|---|---|
| **Sumsub AI Agent Verification** (Jan 2026) | *Who is the human behind this agent?* | Binds agent activity to a verified human identity; targeted liveness at critical moments (onboarding, account changes, high-value payouts); device intelligence; risk scoring | Identity vendor, called by the business |
| **Visa Trusted Agent Protocol** (Oct 2025, with Cloudflare) | *Is this a trusted agent or a malicious bot?* | Cryptographically signed HTTP messages carry the agent's intent, the verified user identity and payment details; merchants check them with Visa's public keys | Merchant edge |
| **Mastercard Verifiable Intent** (2026, with Google) | *What exactly did the user authorise?* | A tamper-resistant record of the user's spending mandate, aligned with Google's AP2/UCP and built on FIDO, EMVCo, IETF and W3C standards; integrated into Agent Pay | Card network |
| **Skyfire KYAPay** (Jun 2025) | *Which agent, platform and user is this, and how does it pay?* | Open protocol: agent identity plus tokenised payment credentials declaring user intent | Agent-to-business |
| **NPCI Unified Agent Protocol** (reported, 2026) | *How do agents pay on UPI?* | Reported: an agent registry, delegation reusing UPI Circle and Reserve Pay, low-value use cases first; liability framework not yet published | National UPI rails |
| **This prototype** | *Is this specific request inside a mandate the human signed, and is it normal?* | Issuer-signed mandate bound to the agent's key and the human's face template; 9-layer bank-side verifier; face step-up; hash-chained audit | Bank / PSP, on UPI-style semantics |

These approaches are complementary, not rivals. Sumsub-style binding tells you *who* delegated. Mastercard-style
intent records *what* was delegated. What I wanted to test was the step in between: a bank enforcing the delegation
deterministically on every request, and assuming the agent may already be compromised.

## What the prototype adds

1. **Enforcement outside the model.** Scope lives in an issuer-signed mandate that the model never sees. A hijacked
   agent can *ask* for ₹50,000; L7 refuses because the signed cap is ₹5,000. NPCI's chairman made the same point
   at GFF 2026: AI "may recommend", but authentication and settlement must be deterministic and auditable.
2. **Proof of possession.** The mandate names the agent's public key and every request is signed with it. A leaked
   mandate is inert.
3. **The bank as source of truth for context.** The merchant category comes from the bank's registry, and the
   behavioural baseline comes from the human's own history, not the agent's.
4. **Face-bound step-up.** Risky-but-permitted payments pause until the same human who granted the mandate passes a
   random head-turn challenge. The mandate commits to their template hash, so swapping the template breaks the binding.
5. **Evidence for disputes.** Every decision, consent and biometric event is hash-chained with signed checkpoints.
   When NPCI's liability framework lands, this is the kind of record it will need.

## What an Indian version needs

- **UPI primitives, not card tokens.** Most agent payments here will be UPI. The prototype's caps are UPI Circle's
  full-delegation limits (₹5,000 per payment, ₹15,000 per month), and its step-up mirrors partial delegation, where
  the primary approves.
- **RBI's authentication directions** (in force since 1 April 2026) need an answer for "who authenticated this?".
  Pre-authorised delegation plus a risk-based additional factor is one answer.
- **DPDP consent that can be withdrawn.** The mandate carries purpose, notice version and withdrawability.
  Withdrawal revokes the mandate *and* erases the face template in one action.
- **A registry hook.** In production, `agent_id` + public key is where an NPCI registry certificate would plug in.
- **Step-up that works for everyone.** It needs vernacular consent notices, low-end phones and assisted users. And
  for step-up to be trusted at national scale, liveness must beat deepfakes and virtual-camera injection, not just
  photos. That is the hard part, and it is identity-verification vendors' home ground.

## Limits of this prototype

Synthetic data only. The head-turn liveness stops photos and replays, not a real-time face swap. Keys live in
SQLite rather than an HSM. The 37 eval scenarios were written alongside the code, so they show coverage, not
independent robustness.

## Sources

- Sumsub: [AI Agent Verification introduces agent-to-human binding](https://www.prnewswire.com/news-releases/sumsubs-ai-agent-verification-introduces-agent-to-human-binding-to-establish-human-accountability-in-ai-302673467.html) (29 Jan 2026); [PYMNTS coverage](https://www.pymnts.com/news/artificial-intelligence/2026/sumsub-adds-ai-verification-know-your-agent-framework/)
- Visa: [Trusted Agent Protocol](https://corporate.visa.com/en/sites/visa-perspectives/newsroom/visa-unveils-trusted-agent-protocol-for-ai-commerce.html); [developer spec](https://developer.visa.com/capabilities/trusted-agent-protocol)
- Mastercard: [How Verifiable Intent builds trust in agentic commerce](https://www.mastercard.com/us/en/news-and-trends/stories/2026/verifiable-intent.html); [PYMNTS](https://www.pymnts.com/mastercard/2026/mastercard-unveils-open-standard-to-verify-ai-agent-transactions/)
- Skyfire: [KYAPay protocol launch](https://www.businesswire.com/news/home/20250626772489/en/Skyfire-Launches-Open-KYAPay-Protocol-With-Agent-Checkout); [kyapay.org](https://kyapay.org/)
- NPCI: [MediaNama on GFF 2026 remarks](https://www.medianama.com/2026/09/223-npci-ai-agents-upi-payments/) (10 Sep 2026); [Business Standard on the Unified Agent Protocol](https://www.business-standard.com/finance/news/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol-126070801343_1.html) (Jul 2026); [TNGlobal on the agent registry](https://technode.global/2026/09/12/india-npci-ai-agent-registry-upi-payments/) (12 Sep 2026); [AI in Asia deep dive](https://aiinasia.com/south-asia/npci-unified-agent-protocol-upi-agentic-payments-south-asia-deep-dive-2026-09-02)
- UPI Circle limits: [Paytm explainer](https://paytm.com/blog/payments/upi/what-is-upi-circle-transaction-limits/); [Razorpay explainer](https://razorpay.com/blog/what-is-upi-circle/)
