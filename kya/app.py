"""Web demo: three panels (human, personal agent, bank verifier) + audit log.

Run:  uv run uvicorn kya.app:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import claude_agent, scripted
from .agent.attacks import ATTACKS
from .agent.runtime import AgentRuntime, FakeWeb, InProcessTransport, MCPTransport
from .bank_mcp import build_mcp
from .events import EventBus
from .face import liveness as lv
from .face import template as ft
from .mandate import DAY
from .service import BankService
from .world import USER_ID, World

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEB = ROOT / "web"
PORT = int(os.environ.get("PORT", "8000"))
TRANSPORT = os.environ.get("KYA_TRANSPORT", "mcp")


class State:
    def __init__(self):
        self.events = EventBus()
        self.web = FakeWeb()
        self.face = None
        self.kyc_challenges: dict[str, str] = {}
        self.boot()

    def boot(self) -> None:
        DATA.mkdir(exist_ok=True)
        self.world = World(str(DATA / "kya.db"), self.events, wallet_file=str(DATA / "agent_wallet.json"))
        self.service = BankService(self.world.bank)

    def reset(self) -> None:
        self.world.store.conn.close()
        for name in ("kya.db", "id_photo.jpg"):
            (DATA / name).unlink(missing_ok=True)
        self.boot()

    def transport(self):
        if TRANSPORT == "mcp":
            return MCPTransport(f"http://127.0.0.1:{PORT}/mcp")
        return InProcessTransport(self.service)

    def pipeline(self):
        if self.face is None:
            from .face.pipeline import FacePipeline
            self.face = FacePipeline()
        return self.face


S = State()
mcp = build_mcp(lambda: S.service)
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="KYA: Know Your Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# --- state & live events -----------------------------------------------------------
@app.get("/api/state")
def state() -> dict[str, Any]:
    w = S.world
    store, bank = w.store, w.bank
    user = store.user(USER_ID)
    mandate = w.wallet.mandate
    row = store.mandate_row(mandate["mandate_id"])
    scope = mandate["scope"]
    spent = store.spent_since(mandate["mandate_id"], bank.now() - scope["period_days"] * DAY)
    pending = store.all("SELECT challenge_id, direction, detail, created_at FROM stepups WHERE status='pending' "
                        "ORDER BY created_at DESC")
    return {
        "user": {k: user[k] for k in ("user_id", "name", "account", "kyc_ref", "id_number", "dob", "face_hash")}
        | {"face_enrolled": user["face_template"] is not None,
           "face_source": store.get_kv("face_source") or "synthetic",
           "has_id_photo": (DATA / "id_photo.jpg").exists()},
        "balance_inr": store.balance(user["account"]),
        "agent": {"agent_id": w.wallet.agent_id, "name": w.wallet.name, "pubkey": w.wallet.pubkey},
        "mandate": mandate,
        "mandate_status": {"revoked": row["revoked_at"] is not None, "reason": row["revoke_reason"],
                           "spent_inr": spent, "expired": mandate["exp"] <= bank.now()},
        "issuer": {"kid": bank.kid, "pubkey": bank.issuer_pub},
        "merchants": store.merchants(),
        "pending_stepups": [{**p, "detail": json.loads(p["detail"])} for p in pending],
        "claude": {"available": claude_agent.available(), "model": claude_agent.MODEL},
        "transport": TRANSPORT,
        "attacks": [{"id": a.id, "title": a.title, "threat": a.threat, "layer": a.expected_layer}
                    for a in ATTACKS.values()],
        "tasks": scripted.TASKS,
        "now": bank.now(),
    }


@app.get("/api/events")
async def events(request: Request):
    queue = S.events.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            S.events.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# --- agent ---------------------------------------------------------------------------
class AgentRun(BaseModel):
    task: str
    agent: str = "scripted"
    poisoned: bool = False
    hijacked: bool = True


@app.post("/api/agent/run")
async def agent_run(body: AgentRun):
    if body.agent == "claude" and not claude_agent.available():
        raise HTTPException(400, "Set ANTHROPIC_API_KEY in .env to use Claude")
    S.web.poisoned = body.poisoned
    rt = AgentRuntime(S.world.wallet, S.transport(), S.world.bank.now, S.events, S.web)
    runner = claude_agent.run if body.agent == "claude" else scripted.run
    hijacked = body.hijacked and body.poisoned

    async def go():
        S.events.publish("agent.start", {"agent": body.agent, "poisoned": body.poisoned, "transport": TRANSPORT})
        try:
            await runner(rt, body.task, hijacked=hijacked)
        except Exception as e:  # surface failures in the UI instead of dying silently
            S.events.publish("agent.message", {"text": f"(agent error: {type(e).__name__}: {e})"})
        S.events.publish("agent.done", {"attempts": rt.attempts})

    asyncio.create_task(go())
    return {"started": True}


@app.post("/api/attack/{attack_id}")
def attack(attack_id: str):
    if attack_id not in ATTACKS:
        raise HTTPException(404, "unknown attack")
    a = ATTACKS[attack_id]
    S.events.publish("attack.start", {"id": a.id, "title": a.title, "layer": a.expected_layer})
    steps = a.run(S.world)
    return {"attack": a.id, "expected_layer": a.expected_layer,
            "steps": [{"label": s["label"], "outcome": s["decision"]["outcome"], "layer": s["decision"]["layer"],
                       "reason": s["decision"]["reason"]} for s in steps]}


# --- mandate lifecycle ----------------------------------------------------------------
@app.post("/api/mandate/revoke")
def revoke():
    S.world.bank.revoke(S.world.wallet.mandate["mandate_id"], "user_revoked")
    return {"ok": True}


@app.post("/api/consent/withdraw")
def withdraw():
    S.world.bank.withdraw_consent(S.world.wallet.mandate["mandate_id"])
    S.world.store.set_kv("face_source", "erased")
    return {"ok": True}


class Grant(BaseModel):
    per_txn_cap_inr: int = 5000
    cumulative_cap_inr: int = 15000
    step_up_above_inr: int = 3000
    ttl_days: int = 30


@app.post("/api/mandate/grant")
def grant(body: Grant):
    old = S.world.wallet.mandate
    row = S.world.store.mandate_row(old["mandate_id"])
    if row and row["revoked_at"] is None:
        S.world.bank.revoke(old["mandate_id"], "superseded")
    scope = {**old["scope"], "per_txn_cap_inr": body.per_txn_cap_inr, "cumulative_cap_inr": body.cumulative_cap_inr,
             "step_up_above_inr": body.step_up_above_inr}
    return S.world.grant_mandate(scope=scope, ttl_days=body.ttl_days)


# --- audit ----------------------------------------------------------------------------
@app.get("/api/audit")
def audit():
    a = S.world.bank.audit
    return {"entries": a.entries(), "verify": a.verify()}


class Tamper(BaseModel):
    mode: str
    seq: int | None = None


@app.post("/api/audit/tamper")
def tamper(body: Tamper):
    a = S.world.bank.audit
    if body.mode == "edit":
        a.tamper_edit(body.seq)
    elif body.mode == "rehash":
        a.tamper_edit(body.seq, rehash=True)
    elif body.mode == "delete":
        a.tamper_delete(body.seq)
    elif body.mode == "truncate":
        a.tamper_truncate(2)
    else:
        raise HTTPException(400, "unknown mode")
    return a.verify()


@app.post("/api/reset")
def reset():
    S.reset()
    S.events.publish("reset", {})
    return {"ok": True}


# --- face: KYC enrollment and step-up --------------------------------------------------
@app.post("/api/kyc/challenge")
def kyc_challenge():
    cid, direction = secrets.token_urlsafe(8), secrets.choice(["LEFT", "RIGHT"])
    S.kyc_challenges[cid] = direction
    return {"challenge_id": cid, "direction": direction}


class Enroll(BaseModel):
    challenge_id: str
    id_photo: str
    frames: list[str]


@app.post("/api/kyc/enroll")
async def kyc_enroll(body: Enroll):
    direction = S.kyc_challenges.pop(body.challenge_id, None)
    if direction is None:
        raise HTTPException(400, "unknown or used challenge")
    from .face.pipeline import decode_image

    fp = await asyncio.to_thread(S.pipeline)
    try:
        id_img = decode_image(body.id_photo)
    except ValueError:
        return {"passed": False, "reason": "could not read the ID photo"}
    id_face, why = fp.single_face(id_img)
    if id_face is None:
        return {"passed": False, "reason": f"ID photo: {why}"}
    id_emb = fp.embed(id_img, id_face)
    live = await asyncio.to_thread(fp.analyse_frames, body.frames, direction)
    sim = round(ft.cosine(id_emb, live["embedding"]), 3) if live["embedding"] is not None else None
    result = {"liveness": live["liveness"], "similarity": sim, "threshold": ft.MATCH_THRESHOLD}
    if not live["liveness"]["passed"]:
        return {**result, "passed": False, "reason": f"liveness failed: {live['liveness']['reason']}"}
    if sim is None or sim < ft.MATCH_THRESHOLD:
        return {**result, "passed": False, "reason": f"live face does not match the ID photo (similarity {sim})"}

    w = S.world
    w.bank.enroll_face(USER_ID, live["embedding"], {"method": "id_photo_match+head_turn_liveness",
                                                    "similarity": sim, "direction": direction})
    w.store.set_kv("face_source", "live")
    import cv2

    cv2.imwrite(str(DATA / "id_photo.jpg"), id_img)
    old = w.wallet.mandate
    row = w.store.mandate_row(old["mandate_id"])
    if row and row["revoked_at"] is None:
        w.bank.revoke(old["mandate_id"], "superseded")
    mandate = w.grant_mandate(scope=old["scope"])
    return {**result, "passed": True, "reason": "identity verified; new mandate issued bound to your face",
            "mandate_id": mandate["mandate_id"]}


def _pending(cid: str) -> dict[str, Any]:
    row = S.world.bank.stepup(cid)
    if row is None:
        raise HTTPException(404, "unknown challenge")
    return row


class Frames(BaseModel):
    frames: list[str]


@app.post("/api/stepup/{cid}/verify")
async def stepup_verify(cid: str, body: Frames):
    row = _pending(cid)
    fp = await asyncio.to_thread(S.pipeline)
    live = await asyncio.to_thread(fp.analyse_frames, body.frames, row["direction"])
    return S.world.bank.resolve_stepup(cid, liveness=live["liveness"], embedding=live["embedding"])


@app.post("/api/stepup/{cid}/photo_attack")
async def stepup_photo_attack(cid: str):
    """Hold a still photo up to the camera: the enrolled ID photo repeated as every frame."""
    row = _pending(cid)
    photo = DATA / "id_photo.jpg"
    if photo.exists():
        import base64

        frame = "data:image/jpeg;base64," + base64.b64encode(photo.read_bytes()).decode()
        fp = await asyncio.to_thread(S.pipeline)
        live = await asyncio.to_thread(fp.analyse_frames, [frame] * 15, row["direction"])
        return S.world.bank.resolve_stepup(cid, liveness={**live["liveness"], "simulated": "photo replay"},
                                           embedding=live["embedding"])
    user = S.world.store.user(USER_ID)
    tmpl = ft.from_blob(user["face_template"]) if user["face_template"] else None
    liveness = {**lv.evaluate(lv.synthetic_track(None), row["direction"]), "simulated": "static landmark track"}
    return S.world.bank.resolve_stepup(cid, liveness=liveness, embedding=tmpl)


@app.post("/api/stepup/{cid}/simulate")
def stepup_simulate(cid: str, who: str = "genuine"):
    """No-webcam fallback: synthetic landmark track + synthetic embedding (clearly labelled as simulated)."""
    row = _pending(cid)
    user = S.world.store.user(USER_ID)
    tmpl = ft.from_blob(user["face_template"]) if user["face_template"] else ft.synthetic(0)
    sim = 0.74 if who == "genuine" else 0.08
    liveness = {**lv.evaluate(lv.synthetic_track(row["direction"]), row["direction"]), "simulated": who}
    return S.world.bank.resolve_stepup(cid, liveness=liveness, embedding=ft.near(tmpl, sim, seed=7))


@app.post("/api/stepup/{cid}/deny")
def stepup_deny(cid: str):
    _pending(cid)
    return S.world.bank.resolve_stepup(cid, deny=True)


@app.get("/api/id_photo")
def id_photo():
    path = DATA / "id_photo.jpg"
    if not path.exists():
        raise HTTPException(404, "no ID photo")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.get("/api/eval")
def eval_results():
    path = ROOT / "evals" / "results.json"
    if not path.exists():
        return JSONResponse({"available": False})
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}


@app.get("/api/mcp/tools")
async def mcp_tools():
    """What the agent runtime discovers over MCP, and what it shows the model (envelope stripped)."""
    tools = await MCPTransport(f"http://127.0.0.1:{PORT}/mcp").list_tools()
    return {"tools": tools}


app.mount("/", mcp_app)  # serves the bank's MCP endpoint at /mcp (must stay last)

