"""Wires a bank, the synthetic seed data and Aarav's agent wallet together."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from . import crypto
from .agent.wallet import AgentWallet
from .bank import Bank
from .events import EventBus
from .seed import seed
from .store import Store

USER_ID = "u_demo_001"
AGENT_ID = "agt_aarav_assistant"


class World:
    def __init__(self, db: str = ":memory:", events: EventBus | None = None,
                 clock: Callable[[], int] | None = None, wallet_file: str | None = None):
        self.store = Store(db)
        self.bank = Bank(self.store, events, clock)
        seed(self.bank)
        self.wallet = _load_wallet(wallet_file)
        self.wallet.install(self.active_mandate() or self.grant_mandate())

    def active_mandate(self) -> dict | None:
        row = self.store.one(
            "SELECT body FROM mandates WHERE agent_id=? AND revoked_at IS NULL ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1", (self.wallet.agent_id,))
        if not row:
            return None
        mandate = json.loads(row["body"])
        return mandate if mandate["exp"] > self.bank.now() else None

    def grant_mandate(self, **kwargs) -> dict:
        """The human grants (or re-grants) a mandate to their agent."""
        mandate = self.bank.issue_mandate(user_id=USER_ID, agent_id=self.wallet.agent_id,
                                          agent_pubkey=self.wallet.pubkey, **kwargs)
        self.wallet.install(mandate)
        return mandate

    def call(self, action: str, **params) -> dict:
        return self.bank.handle(self.wallet.envelope(action, params, self.bank.now()))


def _load_wallet(path: str | None) -> AgentWallet:
    """The agent's key lives on the agent side (a separate file), not in the bank's database."""
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text())
        return AgentWallet(data["agent_id"], data["name"], crypto.key_from_str(data["sk"]))
    wallet = AgentWallet(AGENT_ID, "Aarav's assistant")
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        Path(path).write_text(json.dumps({"agent_id": wallet.agent_id, "name": wallet.name,
                                          "sk": crypto.key_to_str(wallet.sk)}))
    return wallet
