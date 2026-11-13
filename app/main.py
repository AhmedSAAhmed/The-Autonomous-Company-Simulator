"""HTTP surface: one page, a JSON API, and a server-sent event stream."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as C
from .engine import Simulation
from .llm import LLM

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@asynccontextmanager
async def lifespan(app):
    load_env()
    data = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    data.mkdir(parents=True, exist_ok=True)
    app.state.sim = Simulation(seed=int(os.environ.get("SEED", "7")),
                               db_path=str(data / "events.db"), llm=LLM())
    yield
    app.state.sim.pause()


app = FastAPI(title=C.COMPANY_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def sim() -> Simulation:
    return app.state.sim


class Control(BaseModel):
    action: str
    seed: int | None = None
    speed: float | None = None


class Choice(BaseModel):
    choice: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/state")
def state():
    return sim().snapshot()


@app.get("/api/meta")
def meta():
    roles = {a: (ROOT / "company" / "roles" / f"{a}.md").read_text(encoding="utf-8") for a in C.AGENTS}
    return {
        "company": C.COMPANY_NAME, "currency": C.CURRENCY, "agents": C.AGENTS, "roles": roles,
        "max_day": C.MAX_DAY,
        "rules": [
            ["Sales", f"Discount above {C.MAX_AUTO_DISCOUNT}% on a retainer of AED {C.DISCOUNT_ESCALATION_VALUE:,}+",
             f"Counter at {C.MAX_AUTO_DISCOUNT}%"],
            ["Finance", f"Spend above {C.BUDGET_AUTHORITY_PCT}% of cash, or runway under {C.BUDGET_MIN_RUNWAY} days", "Deny"],
            ["Finance", f"Invoice above AED {C.PLAN_AUTHORITY:,} three days overdue after two reminders", "Offer payment plan"],
            ["Support", f"Goodwill credit above AED {C.CREDIT_AUTHORITY}", "No credit"],
            ["Ops", "Contractor spend needed while Finance is down", "Deny"],
            ["Watchdog", f"Any agent misses {C.HEARTBEAT_MISSES} heartbeats", "Restart agent"],
        ],
        "ttl_hours": C.ESCALATION_TTL_TICKS,
    }


@app.get("/api/stream")
async def stream():
    s = sim()
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    s.listeners.add(queue)
    if s.adaptive.state["t"] < 0:
        s.play()  # the first visitor opens the company

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.listeners.discard(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/control")
async def control(body: Control):
    s = sim()
    if body.action == "play":
        s.play()
    elif body.action == "pause":
        s.pause()
    elif body.action == "speed" and body.speed in (1, 2, 4):
        s.speed = body.speed
    elif body.action == "next_day":
        await s.to_day_end()
    elif body.action == "run_week":
        await s.to_week_end()
    elif body.action == "reset":
        async with s.lock:
            s.reset(body.seed)
    else:
        raise HTTPException(400, "unknown action")
    s.publish(reload=body.action in ("next_day", "run_week", "reset"))
    return {"ok": True}


@app.post("/api/inbox/{esc_id}")
async def decide(esc_id: str, body: Choice):
    s = sim()
    async with s.lock:
        if not s.resolve(esc_id, body.choice):
            raise HTTPException(409, "already decided or unknown choice")
        s.publish()
    return {"ok": True}


@app.post("/api/agents/{name}/{action}")
async def agent(name: str, action: str):
    s = sim()
    if name not in C.AGENTS or action not in ("kill", "restore"):
        raise HTTPException(404)
    async with s.lock:
        (s.kill if action == "kill" else s.restore)(name)
        s.publish()
    return {"ok": True}


@app.get("/api/replay/{day}")
def replay(day: int):
    s = sim()
    if str(day) not in s.adaptive.state["day_hashes"]:
        raise HTTPException(404, "that day has not closed yet")
    return s.replay(day)
