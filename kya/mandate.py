"""Mandates: issuer-signed delegations from a verified human to one agent.

Modelled on a UPI AutoPay mandate (caps, merchant categories, validity window,
revocable) plus a DPDP-style consent record. The mandate names the agent's
public key, so only that agent can use it (proof of possession).
"""
from __future__ import annotations

from typing import Any

from nacl.signing import SigningKey
from pydantic import BaseModel, ConfigDict, Field

from . import crypto

MANDATE_VERSION = 1
DAY = 86_400


class Principal(BaseModel):
    user_id: str
    account: str
    kyc_ref: str
    face_hash: str | None = None


class AgentBinding(BaseModel):
    agent_id: str
    pubkey: str


class Scope(BaseModel):
    actions: list[str]
    mcc_allow: list[str]
    per_txn_cap_inr: int = Field(gt=0)
    cumulative_cap_inr: int = Field(gt=0)
    period_days: int = Field(gt=0)
    step_up_above_inr: int = Field(ge=0)


class Consent(BaseModel):
    consent_id: str
    purpose: str
    notice_version: str
    granted_at: int
    withdrawable: bool = True


class Mandate(BaseModel):
    """Structural schema, used by the verifier's envelope check (L1)."""
    model_config = ConfigDict(extra="forbid")

    v: int
    mandate_id: str
    issuer: str
    kid: str
    principal: Principal
    agent: AgentBinding
    scope: Scope
    consent: Consent
    iat: int
    nbf: int
    exp: int
    nonce: str
    sig: str


def default_scope() -> dict[str, Any]:
    return {
        "actions": ["get_balance", "list_merchants", "pay_merchant"],
        "mcc_allow": ["5411", "5814", "4121"],  # grocery, food delivery, cabs
        "per_txn_cap_inr": 5_000,
        "cumulative_cap_inr": 15_000,
        "period_days": 30,
        "step_up_above_inr": 3_000,
    }


def issue_mandate(
    issuer_sk: SigningKey,
    *,
    issuer: str,
    kid: str,
    principal: dict[str, Any],
    agent_id: str,
    agent_pubkey: str,
    scope: dict[str, Any],
    purpose: str,
    now: int,
    ttl_days: int = 30,
    nbf: int | None = None,
) -> dict[str, Any]:
    body = {
        "v": MANDATE_VERSION,
        "mandate_id": crypto.new_id("mdt"),
        "issuer": issuer,
        "kid": kid,
        "principal": principal,
        "agent": {"agent_id": agent_id, "pubkey": agent_pubkey},
        "scope": scope,
        "consent": {
            "consent_id": crypto.new_id("cns"),
            "purpose": purpose,
            "notice_version": "1.0",
            "granted_at": now,
            "withdrawable": True,
        },
        "iat": now,
        "nbf": now if nbf is None else nbf,
        "exp": now + ttl_days * DAY,
        "nonce": crypto.new_nonce(),
    }
    return {**body, "sig": crypto.sign(issuer_sk, body)}


def unsigned(mandate: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in mandate.items() if k != "sig"}


def verify_signature(mandate: dict[str, Any], trusted_issuers: dict[str, str]) -> tuple[bool, str]:
    kid = mandate.get("kid")
    pub = trusted_issuers.get(kid)
    if pub is None:
        return False, f"unknown issuer key id {kid!r}"
    if not crypto.verify(pub, unsigned(mandate), mandate.get("sig", "")):
        return False, f"signature does not verify under trusted key {kid!r} (mandate altered or forged)"
    return True, f"valid Ed25519 signature by {kid}"
