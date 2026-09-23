"""Agent-side key custody.

The wallet lives in the agent *runtime*, outside the model's context. The LLM
can ask for ``pay_merchant(...)``, but it never sees the private key or the
mandate, so it cannot forge, widen or re-sign its own authority.
"""
from __future__ import annotations

from typing import Any

from nacl.signing import SigningKey

from .. import crypto
from ..envelope import build_request, seal


class AgentWallet:
    def __init__(self, agent_id: str, name: str = "Personal agent", sk: SigningKey | None = None):
        self.agent_id = agent_id
        self.name = name
        self.sk = sk or crypto.generate_key()
        self.pubkey = crypto.pubkey(self.sk)
        self.mandate: dict[str, Any] | None = None

    def install(self, mandate: dict[str, Any]) -> None:
        self.mandate = mandate

    def envelope(self, action: str, params: dict[str, Any], ts: int, nonce: str | None = None) -> dict[str, Any]:
        if self.mandate is None:
            raise RuntimeError("no mandate installed")
        request = build_request(action=action, params=params, account=self.mandate["principal"]["account"],
                                mandate_id=self.mandate["mandate_id"], agent_id=self.agent_id, ts=ts, nonce=nonce)
        return seal(self.sk, self.mandate, request)
