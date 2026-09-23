"""The bank's verifier: a fixed pipeline of layered checks on every agent request.

The verifier never trusts the agent. Scope comes only from the issuer-signed
mandate, merchant category comes from the bank's own registry, and every
request must be signed by the key the mandate was issued to.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from . import crypto, risk
from .envelope import request_hash
from .mandate import DAY, Mandate, verify_signature
from .store import Store

LAYERS = [
    ("L1", "Envelope"),
    ("L2", "Mandate signature"),
    ("L3", "Validity window"),
    ("L4", "Revocation & consent"),
    ("L5", "Agent binding (PoP)"),
    ("L6", "Replay protection"),
    ("L7", "Scope"),
    ("L8", "Behaviour risk"),
    ("L9", "Face step-up"),
]
LAYER_NAMES = dict(LAYERS)
MONEY_ACTIONS = {"pay_merchant", "transfer_to_payee"}
REQUEST_FIELDS = {"action": str, "params": dict, "account": str, "mandate_id": str, "agent_id": str,
                  "ts": int, "nonce": str}


@dataclass
class Check:
    layer: str
    name: str
    status: str  # pass | fail | skip | pending | warn
    detail: str


@dataclass
class Decision:
    outcome: str  # ALLOW | BLOCK | STEP_UP
    layer: str | None
    reason: str
    request_hash: str
    checks: list[Check] = field(default_factory=list)
    risk: dict[str, Any] | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Reject(Exception):
    def __init__(self, detail: str):
        self.detail = detail


class Verifier:
    def __init__(self, store: Store, trusted_issuers: dict[str, str], replay_window_s: int = 120):
        self.store = store
        self.trusted_issuers = trusted_issuers
        self.replay_window_s = replay_window_s

    def evaluate(self, envelope: Any, now: int) -> Decision:
        checks: list[Check] = []
        rhash = request_hash(envelope) if isinstance(envelope, dict) else crypto.digest(None)
        summary = _summary(envelope)
        ctx: dict[str, Any] = {}

        steps = [
            ("L1", self._envelope),
            ("L2", self._signature),
            ("L3", self._validity),
            ("L4", self._revocation),
            ("L5", self._binding),
            ("L6", self._replay),
            ("L7", self._scope),
        ]
        for layer, fn in steps:
            try:
                detail = fn(envelope, now, ctx)
            except Reject as r:
                checks.append(Check(layer, LAYER_NAMES[layer], "fail", r.detail))
                _skip_rest(checks, layer)
                return Decision("BLOCK", layer, r.detail, rhash, checks, None, {**summary, **ctx.get("summary", {})})
            checks.append(Check(layer, LAYER_NAMES[layer], "pass", detail))

        summary.update(ctx.get("summary", {}))
        request = envelope["request"]
        if request["action"] not in MONEY_ACTIONS:
            checks.append(Check("L8", LAYER_NAMES["L8"], "pass", "read-only action, no risk scoring"))
            checks.append(Check("L9", LAYER_NAMES["L9"], "skip", "not required"))
            return Decision("ALLOW", None, "within mandate", rhash, checks, None, summary)

        assessment = risk.assess(self.store, envelope["mandate"], request["account"], ctx["merchant"],
                                 ctx["amount"], now)
        hits = [f["detail"] for f in assessment["features"] if f["hit"]]
        if assessment["score"] >= assessment["threshold"]:
            detail = f"risk {assessment['score']} ≥ {assessment['threshold']}: " + "; ".join(hits)
            checks.append(Check("L8", LAYER_NAMES["L8"], "warn", detail))
            checks.append(Check("L9", LAYER_NAMES["L9"], "pending", "live face verification requested"))
            return Decision("STEP_UP", "L8", detail, rhash, checks, assessment, summary)
        detail = f"risk {assessment['score']} < {assessment['threshold']}" + (": " + "; ".join(hits) if hits else "")
        checks.append(Check("L8", LAYER_NAMES["L8"], "pass", detail))
        checks.append(Check("L9", LAYER_NAMES["L9"], "skip", "not required"))
        return Decision("ALLOW", None, "within mandate, low risk", rhash, checks, assessment, summary)

    # --- layers ----------------------------------------------------------
    def _envelope(self, env: Any, now: int, ctx: dict) -> str:
        if not isinstance(env, dict) or env.get("v") != 1:
            raise Reject("not a v1 KYA envelope")
        for key in ("mandate", "request", "agent_sig"):
            if key not in env:
                raise Reject(f"missing '{key}'")
        try:
            Mandate.model_validate(env["mandate"])
        except ValidationError as e:
            raise Reject(f"malformed mandate: {e.errors()[0]['loc']}") from None
        req = env["request"]
        if not isinstance(req, dict):
            raise Reject("request must be an object")
        for name, typ in REQUEST_FIELDS.items():
            if not isinstance(req.get(name), typ) or isinstance(req.get(name), bool):
                raise Reject(f"request.{name} missing or not {typ.__name__}")
        return "well-formed v1 envelope"

    def _signature(self, env: dict, now: int, ctx: dict) -> str:
        ok, detail = verify_signature(env["mandate"], self.trusted_issuers)
        if not ok:
            raise Reject(detail)
        return detail

    def _validity(self, env: dict, now: int, ctx: dict) -> str:
        m = env["mandate"]
        if now < m["nbf"]:
            raise Reject(f"not valid until {_fmt(m['nbf'])}")
        if now >= m["exp"]:
            raise Reject(f"expired at {_fmt(m['exp'])}")
        return f"valid until {_fmt(m['exp'])} ({(m['exp'] - now) // DAY} days left)"

    def _revocation(self, env: dict, now: int, ctx: dict) -> str:
        row = self.store.mandate_row(env["mandate"]["mandate_id"])
        if row is None:
            raise Reject("mandate id not in the bank's registry")
        if row["revoked_at"] is not None:
            what = "consent withdrawn by user (DPDP)" if row["revoke_reason"] == "consent_withdrawn" else "revoked"
            raise Reject(f"{what} at {_fmt(row['revoked_at'])} ({row['revoke_reason']})")
        return "not revoked, consent active"

    def _binding(self, env: dict, now: int, ctx: dict) -> str:
        m, req = env["mandate"], env["request"]
        if req["mandate_id"] != m["mandate_id"]:
            raise Reject("request references a different mandate")
        if req["agent_id"] != m["agent"]["agent_id"]:
            raise Reject(f"request from agent {req['agent_id']!r}, mandate bound to {m['agent']['agent_id']!r}")
        if not crypto.verify(m["agent"]["pubkey"], req, env["agent_sig"]):
            raise Reject("request not signed by the agent key bound in the mandate (stolen mandate or altered request)")
        return f"signed by bound agent key {m['agent']['pubkey'][:18]}…"

    def _replay(self, env: dict, now: int, ctx: dict) -> str:
        req = env["request"]
        skew = now - req["ts"]
        if abs(skew) > self.replay_window_s:
            raise Reject(f"timestamp {skew:+d}s outside ±{self.replay_window_s}s window")
        if not self.store.use_nonce(req["mandate_id"], req["nonce"], now):
            raise Reject("nonce already used (replayed request)")
        return f"fresh nonce, clock skew {skew:+d}s"

    def _scope(self, env: dict, now: int, ctx: dict) -> str:
        m, req = env["mandate"], env["request"]
        scope = m["scope"]
        action = req["action"]
        if req["account"] != m["principal"]["account"]:
            raise Reject(f"account {req['account']} does not belong to the mandate's principal")
        if action not in scope["actions"]:
            ctx["summary"] = _money_summary(req, None)
            raise Reject(f"action '{action}' not permitted by mandate (allowed: {', '.join(scope['actions'])})")
        if action not in MONEY_ACTIONS:
            return f"'{action}' permitted"

        params = req["params"]
        merchant = self.store.merchant(str(params.get("merchant_id", "")))
        ctx["summary"] = _money_summary(req, merchant)
        if merchant is None:
            raise Reject(f"unknown merchant {params.get('merchant_id')!r}")
        amount = params.get("amount_inr")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise Reject("amount_inr must be a positive whole number of rupees")
        if merchant["mcc"] not in scope["mcc_allow"]:
            raise Reject(f"merchant category {merchant['mcc']} ({merchant['category']}) not in allowed "
                         f"categories {', '.join(scope['mcc_allow'])}")
        if amount > scope["per_txn_cap_inr"]:
            raise Reject(f"₹{amount:,} exceeds per-transaction cap ₹{scope['per_txn_cap_inr']:,}")
        spent = self.store.spent_since(m["mandate_id"], now - scope["period_days"] * DAY)
        if spent + amount > scope["cumulative_cap_inr"]:
            raise Reject(f"₹{spent:,} already spent + ₹{amount:,} exceeds {scope['period_days']}-day cap "
                         f"₹{scope['cumulative_cap_inr']:,}")
        ctx["merchant"], ctx["amount"] = merchant, amount
        return (f"{action} ₹{amount:,} to {merchant['name']} (MCC {merchant['mcc']}) within caps; "
                f"₹{spent + amount:,}/₹{scope['cumulative_cap_inr']:,} used")


def _skip_rest(checks: list[Check], failed_layer: str) -> None:
    after = False
    for layer, name in LAYERS:
        if after:
            checks.append(Check(layer, name, "skip", "not evaluated"))
        if layer == failed_layer:
            after = True


def _summary(env: Any) -> dict[str, Any]:
    try:
        req = env["request"]
        return {"action": req.get("action"), "params": req.get("params"), "account": req.get("account"),
                "mandate_id": env["mandate"].get("mandate_id"), "agent_id": req.get("agent_id")}
    except (TypeError, KeyError, AttributeError):
        return {}


def _money_summary(req: dict, merchant: dict | None) -> dict[str, Any]:
    params = req.get("params", {})
    out = {"amount_inr": params.get("amount_inr")}
    if merchant:
        out.update(merchant_name=merchant["name"], mcc=merchant["mcc"], category=merchant["category"])
    elif "payee_vpa" in params:
        out.update(merchant_name=params["payee_vpa"])
    return out


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, risk.IST).strftime("%d %b %Y %H:%M IST")
