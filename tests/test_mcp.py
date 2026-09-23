"""The bank MCP server end to end, in-process (no network)."""
import asyncio

from mcp.shared.memory import create_connected_server_and_client_session

from kya.agent.runtime import parse_tool_result, strip_envelope
from kya.bank_mcp import build_mcp
from kya.service import BankService


def call(world, tool, args):
    mcp = build_mcp(lambda: BankService(world.bank))

    async def go():
        async with create_connected_server_and_client_session(mcp._mcp_server) as session:
            res = await session.call_tool(tool, args)
            tools = (await session.list_tools()).tools
            return res, tools

    return asyncio.run(go())


def outcome_of(res):
    d = parse_tool_result(res)["decision"]
    return d["outcome"], d["layer"]


def test_pay_over_mcp(world):
    args = {"merchant_id": "m_tiffin", "amount_inr": 380}
    env = world.wallet.envelope("pay_merchant", args, world.bank.now())
    res, _ = call(world, "pay_merchant", {**args, "envelope": env})
    assert outcome_of(res) == ("ALLOW", None)


def test_args_must_match_signed_request(world):
    env = world.wallet.envelope("pay_merchant", {"merchant_id": "m_tiffin", "amount_inr": 380}, world.bank.now())
    res, _ = call(world, "pay_merchant", {"merchant_id": "m_tiffin", "amount_inr": 3800, "envelope": env})
    assert outcome_of(res) == ("BLOCK", "L1")


def test_model_never_sees_envelope(world):
    env = world.wallet.envelope("get_balance", {}, world.bank.now())
    _, tools = call(world, "get_balance", {"envelope": env})
    pay = next(t for t in tools if t.name == "pay_merchant")
    assert "envelope" in pay.inputSchema["properties"]
    stripped = strip_envelope({"name": pay.name, "description": pay.description, "input_schema": pay.inputSchema})
    assert "envelope" not in stripped["input_schema"]["properties"]
    assert "envelope" not in stripped["input_schema"]["required"]
