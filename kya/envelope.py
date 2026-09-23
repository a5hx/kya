"""Agent-side request envelopes.

The agent *runtime* (not the LLM) holds the agent key and the mandate. Every
tool call is wrapped as ``{mandate, request, agent_sig}`` where ``agent_sig``
covers the request, including a fresh nonce and timestamp.
"""
from __future__ import annotations

from typing import Any

from nacl.signing import SigningKey

from . import crypto

ENVELOPE_VERSION = 1


def build_request(
    *, action: str, params: dict[str, Any], account: str, mandate_id: str, agent_id: str, ts: int,
    nonce: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "params": params,
        "account": account,
        "mandate_id": mandate_id,
        "agent_id": agent_id,
        "ts": ts,
        "nonce": nonce or crypto.new_nonce(),
    }


def seal(agent_sk: SigningKey, mandate: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    return {"v": ENVELOPE_VERSION, "mandate": mandate, "request": request, "agent_sig": crypto.sign(agent_sk, request)}


def request_hash(envelope: dict[str, Any]) -> str:
    return crypto.digest(envelope.get("request", {}))
