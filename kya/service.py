"""Async front door to the bank, shared by the MCP server and the in-process transport.

A STEP_UP decision pauses the agent's call until the human finishes (or
declines, or lets it time out) the live face check.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .bank import STEP_UP_TTL_S, Bank


class BankService:
    def __init__(self, bank: Bank, poll_s: float = 0.4):
        self.bank = bank
        self.poll_s = poll_s

    async def submit(self, envelope: Any, wait_for_human: bool = True) -> dict[str, Any]:
        out = self.bank.handle(envelope)
        if out["decision"]["outcome"] != "STEP_UP" or not wait_for_human:
            return out
        cid = out["challenge"]["challenge_id"]
        deadline = time.monotonic() + STEP_UP_TTL_S
        while time.monotonic() < deadline:
            row = self.bank.stepup(cid)
            if row and row["status"] != "pending":
                out["stepup"] = json.loads(row["resolution"]) if row["resolution"] else {"outcome": "BLOCK"}
                return out
            await asyncio.sleep(self.poll_s)
        out["stepup"] = self.bank.resolve_stepup(cid, expired=True)
        return out


def final_outcome(out: dict[str, Any]) -> dict[str, Any]:
    """Collapse a bank response into what the agent (and the model) should see."""
    d = out["decision"]
    if d["outcome"] == "STEP_UP":
        s = out.get("stepup") or {}
        outcome = s.get("outcome", "PENDING")
        return {"outcome": outcome, "layer": "L9", "reason": s.get("reason", "waiting for the account holder"),
                "stepped_up": True, "result": s.get("result")}
    return {"outcome": d["outcome"], "layer": d["layer"], "reason": d["reason"], "stepped_up": False,
            "result": out.get("result")}
