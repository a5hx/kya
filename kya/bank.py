"""The bank: issues mandates, runs the verifier, executes allowed actions,
manages face step-ups and writes every decision to the audit log."""
from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable

import numpy as np

from . import crypto
from .audit import AuditLog
from .events import EventBus, NullBus
from .face import template as ft
from .mandate import default_scope, issue_mandate
from .store import Store
from .verifier import Check, Verifier

ISSUER = "demobank-kya"
KID = "issuer-2026-01"
STEP_UP_TTL_S = 120
MAX_STEP_UP_FAILURES = 3


class Bank:
    def __init__(self, store: Store, events: EventBus | None = None, clock: Callable[[], int] | None = None):
        self.store = store
        self.events = events or NullBus()
        self.clock = clock or (lambda: int(time.time()))
        keys = store.get_kv("bank_keys")
        if keys is None:
            # Demo only: production keys live in an HSM, never in the application database.
            keys = {"issuer": crypto.key_to_str(crypto.generate_key()),
                    "audit": crypto.key_to_str(crypto.generate_key()), "kid": KID}
            store.set_kv("bank_keys", keys)
        self.kid = keys["kid"]
        self.issuer_sk = crypto.key_from_str(keys["issuer"])
        self.issuer_pub = crypto.pubkey(self.issuer_sk)
        self.audit = AuditLog(store, crypto.key_from_str(keys["audit"]))
        self.verifier = Verifier(store, {self.kid: self.issuer_pub})

    def now(self) -> int:
        return int(self.clock())

    def _log(self, kind: str, payload: dict[str, Any]) -> None:
        entry = self.audit.append(kind, payload, self.now())
        self.events.publish("audit", {"seq": entry["seq"], "kind": kind})

    # --- mandates -----------------------------------------------------------
    def issue_mandate(self, *, user_id: str, agent_id: str, agent_pubkey: str, scope: dict | None = None,
                      purpose: str = "Groceries, food delivery and cabs", ttl_days: int = 30,
                      nbf: int | None = None, iat: int | None = None) -> dict[str, Any]:
        user = self.store.user(user_id)
        if user is None:
            raise KeyError(user_id)
        principal = {"user_id": user_id, "account": user["account"], "kyc_ref": user["kyc_ref"],
                     "face_hash": user["face_hash"]}
        mandate = issue_mandate(self.issuer_sk, issuer=ISSUER, kid=self.kid, principal=principal,
                                agent_id=agent_id, agent_pubkey=agent_pubkey, scope=scope or default_scope(),
                                purpose=purpose, now=self.now() if iat is None else iat, ttl_days=ttl_days, nbf=nbf)
        self.store.save_mandate(mandate)
        self._log("mandate.issued", {"mandate_id": mandate["mandate_id"], "user_id": user_id, "agent_id": agent_id,
                                     "scope": mandate["scope"], "consent": mandate["consent"],
                                     "face_hash": principal["face_hash"], "exp": mandate["exp"]})
        self.events.publish("mandate.issued", {"mandate": mandate})
        return mandate

    def revoke(self, mandate_id: str, reason: str = "user_revoked") -> None:
        self.store.revoke(mandate_id, reason, self.now())
        self._log("mandate.revoked", {"mandate_id": mandate_id, "reason": reason})
        self.events.publish("mandate.revoked", {"mandate_id": mandate_id, "reason": reason})

    def withdraw_consent(self, mandate_id: str, erase_biometrics: bool = True) -> None:
        """DPDP-style withdrawal: as easy as granting. Revokes the mandate and erases the face template."""
        row = self.store.mandate_row(mandate_id)
        self.revoke(mandate_id, "consent_withdrawn")
        if erase_biometrics and row:
            self.store.exec("UPDATE users SET face_template=NULL WHERE user_id=?", (row["user_id"],))
            self._log("biometric.erased", {"user_id": row["user_id"], "mandate_id": mandate_id})

    def enroll_face(self, user_id: str, embedding: np.ndarray, evidence: dict[str, Any]) -> str:
        h = ft.template_hash(embedding)
        self.store.exec("UPDATE users SET face_template=?, face_hash=? WHERE user_id=?",
                        (ft.to_blob(embedding), h, user_id))
        self._log("kyc.face_enrolled", {"user_id": user_id, "face_hash": h, **evidence})
        return h

    # --- agent requests -------------------------------------------------------
    def handle(self, envelope: Any) -> dict[str, Any]:
        now = self.now()
        decision = self.verifier.evaluate(envelope, now)
        out: dict[str, Any] = {"decision": decision.to_dict()}
        if decision.outcome == "ALLOW":
            out["result"] = self._execute(envelope, now)
        elif decision.outcome == "STEP_UP":
            out["challenge"] = self._open_stepup(envelope, decision.to_dict(), now)
        s = decision.summary
        self._log("decision", {"outcome": decision.outcome, "layer": decision.layer, "reason": decision.reason,
                               "request_hash": decision.request_hash, "mandate_id": s.get("mandate_id"),
                               "agent_id": s.get("agent_id"), "action": s.get("action"),
                               "amount_inr": s.get("amount_inr"), "merchant": s.get("merchant_name"),
                               "risk_score": (decision.risk or {}).get("score")})
        self.events.publish("decision", out)
        return out

    def reject(self, envelope: Any, layer: str, reason: str) -> dict[str, Any]:
        """Block a request before the verifier runs (e.g. MCP tool args disagree with the signed request)."""
        from .envelope import request_hash
        from .verifier import LAYERS

        checks = [Check(layer, dict(LAYERS)[layer], "fail", reason).__dict__]
        decision = {"outcome": "BLOCK", "layer": layer, "reason": reason, "checks": checks, "risk": None,
                    "request_hash": request_hash(envelope) if isinstance(envelope, dict) else None, "summary": {}}
        self._log("decision", {"outcome": "BLOCK", "layer": layer, "reason": reason,
                               "request_hash": decision["request_hash"]})
        out = {"decision": decision}
        self.events.publish("decision", out)
        return out

    def _execute(self, envelope: dict[str, Any], now: int) -> dict[str, Any]:
        req = envelope["request"]
        action, account = req["action"], req["account"]
        if action == "get_balance":
            return {"account": account, "balance_inr": self.store.balance(account)}
        if action == "list_merchants":
            return {"merchants": [{k: m[k] for k in ("merchant_id", "name", "category", "mcc")}
                                  for m in self.store.merchants()]}
        if action == "pay_merchant":
            merchant = self.store.merchant(req["params"]["merchant_id"])
            amount = req["params"]["amount_inr"]
            with self.store.lock:
                balance = self.store.balance(account) or 0
                if balance < amount:
                    return {"status": "failed", "error": "insufficient funds"}
                txn_id = crypto.new_id("txn")
                self.store.exec("UPDATE accounts SET balance_inr=balance_inr-? WHERE account=?", (amount, account))
                self.store.exec(
                    "INSERT INTO txns(txn_id, account, mandate_id, merchant_id, mcc, amount_inr, ts, source, status) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (txn_id, account, req["mandate_id"], merchant["merchant_id"], merchant["mcc"], amount, now,
                     "agent", "settled"))
            return {"status": "paid", "txn_id": txn_id, "merchant": merchant["name"], "amount_inr": amount,
                    "balance_inr": self.store.balance(account)}
        return {"status": "failed", "error": f"no executor for {action}"}

    # --- face step-up ------------------------------------------------------------
    def _open_stepup(self, envelope: dict[str, Any], decision: dict[str, Any], now: int) -> dict[str, Any]:
        challenge = {"challenge_id": crypto.new_id("chl"), "direction": secrets.choice(["LEFT", "RIGHT"]),
                     "expires_at": now + STEP_UP_TTL_S}
        self.store.exec(
            "INSERT INTO stepups(challenge_id, mandate_id, user_id, request_hash, envelope, direction, status, "
            "attempts, created_at, detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (challenge["challenge_id"], envelope["mandate"]["mandate_id"], envelope["mandate"]["principal"]["user_id"],
             decision["request_hash"], json.dumps(envelope), challenge["direction"], "pending", 0, now,
             json.dumps(decision)))
        self.events.publish("stepup.requested", {**challenge, "decision": decision})
        return challenge

    def stepup(self, challenge_id: str) -> dict[str, Any] | None:
        return self.store.one("SELECT * FROM stepups WHERE challenge_id=?", (challenge_id,))

    def resolve_stepup(self, challenge_id: str, *, liveness: dict[str, Any] | None = None,
                       embedding: np.ndarray | None = None, deny: bool = False,
                       expired: bool = False) -> dict[str, Any]:
        row = self.stepup(challenge_id)
        if row is None:
            raise KeyError(challenge_id)
        if row["status"] != "pending":
            return {"outcome": "BLOCK", "layer": "L9", "reason": f"challenge already {row['status']}"}
        now = self.now()
        envelope = json.loads(row["envelope"])
        mandate = envelope["mandate"]
        face: dict[str, Any] = {}

        if deny:
            passed, reason = False, "human declined the request"
        elif expired or now > row["created_at"] + STEP_UP_TTL_S:
            passed, reason = False, "step-up timed out"
        else:
            user = self.store.user(row["user_id"]) or {}
            template = ft.from_blob(user["face_template"]) if user.get("face_template") else None
            face["liveness"] = liveness or {"passed": False, "reason": "no liveness evidence"}
            face["template_bound"] = template is not None and ft.template_hash(template) == mandate["principal"]["face_hash"]
            face["similarity"] = round(ft.cosine(embedding, template), 3) if (embedding is not None and template is not None) else None
            face["threshold"] = ft.MATCH_THRESHOLD
            if not face["liveness"].get("passed"):
                passed, reason = False, f"liveness failed: {face['liveness'].get('reason', 'unknown')}"
            elif template is None:
                passed, reason = False, "no enrolled face template (erased or never enrolled)"
            elif not face["template_bound"]:
                passed, reason = False, "enrolled template does not match the face hash committed in the mandate"
            elif face["similarity"] is None or face["similarity"] < ft.MATCH_THRESHOLD:
                passed, reason = False, f"face does not match the mandate's principal (similarity {face['similarity']})"
            else:
                passed, reason = True, f"live, same person (similarity {face['similarity']} ≥ {ft.MATCH_THRESHOLD})"

        status = "approved" if passed else ("denied" if deny else "failed")
        self.store.exec("UPDATE stepups SET status=?, resolved_at=?, attempts=attempts+1 WHERE challenge_id=?",
                        (status, now, challenge_id))
        out: dict[str, Any] = {"challenge_id": challenge_id, "outcome": "ALLOW" if passed else "BLOCK",
                               "layer": "L9", "reason": reason, "face": face,
                               "check": Check("L9", "Face step-up", "pass" if passed else "fail", reason).__dict__}
        if passed:
            out["result"] = self._execute(envelope, now)
        else:
            key = f"stepup_failures:{mandate['mandate_id']}"
            failures = (self.store.get_kv(key) or 0) + (0 if deny else 1)
            self.store.set_kv(key, failures)
            if failures >= MAX_STEP_UP_FAILURES:
                self.revoke(mandate["mandate_id"], "stepup_failures")
                out["mandate_revoked"] = True
        self.store.exec("UPDATE stepups SET resolution=? WHERE challenge_id=?", (json.dumps(out), challenge_id))
        req = envelope["request"]
        self._log("stepup.resolved", {"challenge_id": challenge_id, "outcome": out["outcome"], "reason": reason,
                                      "request_hash": row["request_hash"], "mandate_id": mandate["mandate_id"],
                                      "amount_inr": req["params"].get("amount_inr"),
                                      "similarity": face.get("similarity"),
                                      "liveness": (face.get("liveness") or {}).get("passed")})
        self.events.publish("stepup.resolved", out)
        return out
