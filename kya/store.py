"""SQLite persistence for the bank side (users, mandates, revocations, nonces, txns, audit)."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id TEXT PRIMARY KEY, name TEXT, account TEXT, kyc_ref TEXT, id_number TEXT, dob TEXT,
  face_template BLOB, face_hash TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS accounts(account TEXT PRIMARY KEY, user_id TEXT, balance_inr INTEGER);
CREATE TABLE IF NOT EXISTS merchants(merchant_id TEXT PRIMARY KEY, name TEXT, mcc TEXT, category TEXT, vpa TEXT);
CREATE TABLE IF NOT EXISTS mandates(
  mandate_id TEXT PRIMARY KEY, user_id TEXT, agent_id TEXT, body TEXT, created_at INTEGER,
  revoked_at INTEGER, revoke_reason TEXT);
CREATE TABLE IF NOT EXISTS nonces(mandate_id TEXT, nonce TEXT, ts INTEGER, PRIMARY KEY(mandate_id, nonce));
CREATE TABLE IF NOT EXISTS txns(
  txn_id TEXT PRIMARY KEY, account TEXT, mandate_id TEXT, merchant_id TEXT, mcc TEXT,
  amount_inr INTEGER, ts INTEGER, source TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS stepups(
  challenge_id TEXT PRIMARY KEY, mandate_id TEXT, user_id TEXT, request_hash TEXT, envelope TEXT,
  direction TEXT, status TEXT, attempts INTEGER, created_at INTEGER, resolved_at INTEGER, detail TEXT,
  resolution TEXT);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts INTEGER, kind TEXT, payload TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS checkpoints(seq INTEGER PRIMARY KEY, head_hash TEXT, sig TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # --- generic helpers -------------------------------------------------
    def exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_kv(self, key: str) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else None

    def set_kv(self, key: str, value: Any) -> None:
        self.exec("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    # --- domain helpers ---------------------------------------------------
    def user(self, user_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE user_id=?", (user_id,))

    def merchant(self, merchant_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM merchants WHERE merchant_id=?", (merchant_id,))

    def merchants(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM merchants ORDER BY name")

    def balance(self, account: str) -> int | None:
        row = self.one("SELECT balance_inr FROM accounts WHERE account=?", (account,))
        return row["balance_inr"] if row else None

    def save_mandate(self, mandate: dict[str, Any]) -> None:
        self.exec(
            "INSERT INTO mandates(mandate_id, user_id, agent_id, body, created_at) VALUES(?,?,?,?,?)",
            (mandate["mandate_id"], mandate["principal"]["user_id"], mandate["agent"]["agent_id"],
             json.dumps(mandate), mandate["iat"]),
        )

    def mandate_row(self, mandate_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM mandates WHERE mandate_id=?", (mandate_id,))

    def revoke(self, mandate_id: str, reason: str, ts: int) -> None:
        self.exec(
            "UPDATE mandates SET revoked_at=?, revoke_reason=? WHERE mandate_id=? AND revoked_at IS NULL",
            (ts, reason, mandate_id),
        )

    def use_nonce(self, mandate_id: str, nonce: str, ts: int) -> bool:
        """Record a request nonce. False if it was already seen (replay)."""
        try:
            self.exec("INSERT INTO nonces(mandate_id, nonce, ts) VALUES(?,?,?)", (mandate_id, nonce, ts))
            return True
        except sqlite3.IntegrityError:
            return False

    def spent_since(self, mandate_id: str, since_ts: int) -> int:
        row = self.one(
            "SELECT COALESCE(SUM(amount_inr),0) AS s FROM txns WHERE mandate_id=? AND status='settled' AND ts>=?",
            (mandate_id, since_ts),
        )
        return int(row["s"])

    def agent_txn_count_since(self, mandate_id: str, since_ts: int) -> int:
        row = self.one(
            "SELECT COUNT(*) AS c FROM txns WHERE mandate_id=? AND status='settled' AND ts>=?",
            (mandate_id, since_ts),
        )
        return int(row["c"])

    def history(self, account: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM txns WHERE account=? AND status='settled' ORDER BY ts", (account,)
        )
