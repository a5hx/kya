"""Tamper-evident audit log.

Each entry stores ``hash = sha256(prev_hash || canonical(entry))``. Editing any
entry breaks its own hash; re-hashing it breaks the next entry's link. After
every append the bank signs the head (seq, hash) as a checkpoint, which catches
deletion of the newest entries (truncation). In production the checkpoints
would be published outside the database (regulator, transparency log).
"""
from __future__ import annotations

import json
from typing import Any

from nacl.signing import SigningKey

from . import crypto
from .store import Store

GENESIS = "0" * 64


def entry_hash(prev_hash: str, seq: int, ts: int, kind: str, payload: dict[str, Any]) -> str:
    body = {"seq": seq, "ts": ts, "kind": kind, "payload": payload}
    return crypto.sha256_hex(prev_hash.encode() + crypto.canonical(body))


class AuditLog:
    def __init__(self, store: Store, signer: SigningKey):
        self.store = store
        self.signer = signer
        self.signer_pub = crypto.pubkey(signer)

    def append(self, kind: str, payload: dict[str, Any], ts: int) -> dict[str, Any]:
        with self.store.lock:
            last = self.store.one("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else GENESIS
            h = entry_hash(prev, seq, ts, kind, payload)
            self.store.exec(
                "INSERT INTO audit(seq, ts, kind, payload, prev_hash, hash) VALUES(?,?,?,?,?,?)",
                (seq, ts, kind, json.dumps(payload, sort_keys=True), prev, h),
            )
            checkpoint = {"seq": seq, "head_hash": h}
            self.store.exec(
                "INSERT OR REPLACE INTO checkpoints(seq, head_hash, sig, ts) VALUES(?,?,?,?)",
                (seq, h, crypto.sign(self.signer, checkpoint), ts),
            )
        return {"seq": seq, "ts": ts, "kind": kind, "payload": payload, "prev_hash": prev, "hash": h}

    def entries(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store.all("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return list(reversed(rows))

    def verify(self) -> dict[str, Any]:
        """Walk the chain. Returns validity, per-entry status and the first broken seq."""
        rows = self.store.all("SELECT * FROM audit ORDER BY seq")
        status: dict[int, str] = {}
        first_broken: int | None = None
        reason = ""
        prev = GENESIS
        expected_seq = 1
        for r in rows:
            problem = ""
            if r["seq"] != expected_seq:
                problem = f"missing entry #{expected_seq} (deleted)"
            elif r["prev_hash"] != prev:
                problem = "prev_hash does not link to the previous entry"
            else:
                try:
                    payload = json.loads(r["payload"])
                except json.JSONDecodeError:
                    payload = None
                if payload is None or entry_hash(prev, r["seq"], r["ts"], r["kind"], payload) != r["hash"]:
                    problem = "content does not match its hash (entry edited)"
            if problem and first_broken is None:
                first_broken, reason = r["seq"], problem
            status[r["seq"]] = "broken" if first_broken is not None else "ok"
            prev = r["hash"]
            expected_seq = r["seq"] + 1

        # Signed checkpoint: the newest signed head must still be present and unchanged.
        cp = self.store.one("SELECT * FROM checkpoints ORDER BY seq DESC LIMIT 1")
        checkpoint_ok = True
        checkpoint_detail = "no checkpoint yet"
        if cp:
            sig_ok = crypto.verify(self.signer_pub, {"seq": cp["seq"], "head_hash": cp["head_hash"]}, cp["sig"])
            row = next((r for r in rows if r["seq"] == cp["seq"]), None)
            if not sig_ok:
                checkpoint_ok, checkpoint_detail = False, "checkpoint signature invalid"
            elif row is None:
                checkpoint_ok = False
                checkpoint_detail = f"signed head #{cp['seq']} is missing (log truncated)"
            elif row["hash"] != cp["head_hash"]:
                checkpoint_ok = False
                checkpoint_detail = f"entry #{cp['seq']} differs from signed head"
            else:
                checkpoint_detail = f"signed head #{cp['seq']} matches"
            if not checkpoint_ok and first_broken is None:
                first_broken, reason = cp["seq"], checkpoint_detail

        return {
            "valid": first_broken is None and checkpoint_ok,
            "length": len(rows),
            "first_broken": first_broken,
            "reason": reason,
            "status": status,
            "checkpoint": {"ok": checkpoint_ok, "detail": checkpoint_detail},
        }

    # --- deliberate tampering, used by the demo UI and the eval ------------
    def tamper_edit(self, seq: int, rehash: bool = False) -> None:
        """Simulate an insider editing an entry (optionally re-hashing it to hide the edit)."""
        row = self.store.one("SELECT * FROM audit WHERE seq=?", (seq,))
        if not row:
            raise KeyError(seq)
        payload = json.loads(row["payload"])
        payload["tampered"] = True
        if "amount_inr" in payload:
            payload["amount_inr"] = 1
        if "outcome" in payload:
            payload["outcome"] = "ALLOW"
        new_hash = entry_hash(row["prev_hash"], seq, row["ts"], row["kind"], payload) if rehash else row["hash"]
        self.store.exec(
            "UPDATE audit SET payload=?, hash=? WHERE seq=?", (json.dumps(payload, sort_keys=True), new_hash, seq)
        )

    def tamper_delete(self, seq: int) -> None:
        self.store.exec("DELETE FROM audit WHERE seq=?", (seq,))

    def tamper_truncate(self, n: int) -> None:
        self.store.exec("DELETE FROM audit WHERE seq IN (SELECT seq FROM audit ORDER BY seq DESC LIMIT ?)", (n,))
