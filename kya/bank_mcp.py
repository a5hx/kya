"""The bank's MCP server. Every tool takes the human-readable arguments plus a
signed KYA ``envelope``. The signed request is authoritative: if the plain
arguments disagree with it, the call is rejected."""
from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from .service import BankService

INSTRUCTIONS = """DemoBank KYA gateway. Each call must include `envelope`: an issuer-signed mandate plus a request
signed by the agent key that mandate names. The bank enforces the mandate's scope, caps and validity, and may pause
risky payments until the account holder passes a live face check."""


def build_mcp(get_service: Callable[[], BankService]) -> FastMCP:
    mcp = FastMCP("kya-bank", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)

    async def submit(action: str, params: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        service = get_service()
        req = envelope.get("request") if isinstance(envelope, dict) else None
        if not isinstance(req, dict) or req.get("action") != action or req.get("params") != params:
            return service.bank.reject(envelope, "L1", "tool arguments do not match the signed request")
        return await service.submit(envelope)

    @mcp.tool()
    async def get_balance(envelope: dict) -> dict:
        """Current balance of the mandate holder's account."""
        return await submit("get_balance", {}, envelope)

    @mcp.tool()
    async def list_merchants(envelope: dict) -> dict:
        """Merchants registered with the bank (id, name, category, MCC)."""
        return await submit("list_merchants", {}, envelope)

    @mcp.tool()
    async def pay_merchant(merchant_id: str, amount_inr: int, envelope: dict) -> dict:
        """Pay a registered merchant. The merchant's category comes from the bank's registry, not the caller."""
        return await submit("pay_merchant", {"merchant_id": merchant_id, "amount_inr": amount_inr}, envelope)

    @mcp.tool()
    async def transfer_to_payee(payee_vpa: str, amount_inr: int, envelope: dict) -> dict:
        """Person-to-person transfer to a UPI ID."""
        return await submit("transfer_to_payee", {"payee_vpa": payee_vpa, "amount_inr": amount_inr}, envelope)

    return mcp
