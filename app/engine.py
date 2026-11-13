"""Runs two companies against one market: the adaptive one and a static baseline.

Both share a seed, so they see the same leads, the same customer luck and the same
failures. The only difference is whether the agents listen to each other.
"""
import asyncio

from . import config as C
from .describe import describe
from .state import fold, kpis, state_hash
from .store import EventStore
from .world import World


class Simulation:
    def __init__(self, seed=7, db_path=":memory:", llm=None):
        self.store = EventStore(db_path)
        self.llm = llm
        self.lock = asyncio.Lock()
        self.listeners = set()
        self.running = False
        self.speed = 1.0
        self.task = None
        self.reset(seed)

    def reset(self, seed=None):
        self.seed = self.seed if seed is None else seed
        self.store.clear()
        self.adaptive = World("adaptive", self.seed, True, self.store, self.llm)
        self.baseline = World("baseline", self.seed, False, self.store)
        self.adaptive.on_restart = self.restore
        self.worlds = [self.adaptive, self.baseline]
        self.store.commit()
        self.running = False

    @property
    def finished(self):
        return self.adaptive.state["t"] + 1 >= C.MAX_DAY * C.TICKS_PER_DAY

    # ---- time --------------------------------------------------------------

    async def tick(self, use_llm=False):
        if self.finished:
            self.running = False
            return
        for world in self.worlds:
            await world.tick(use_llm)

    async def advance(self, ticks, use_llm=False):
        async with self.lock:
            for _ in range(ticks):
                await self.tick(use_llm)
            self.publish(reload=ticks > 1)

    async def to_day_end(self):
        hour = self.adaptive.state["hour"] if self.adaptive.state["t"] >= 0 else -1
        await self.advance((C.TICKS_PER_DAY - 1 - hour) or C.TICKS_PER_DAY)

    async def to_week_end(self):
        per_week = C.TICKS_PER_DAY * C.DAYS_PER_WEEK
        done = (self.adaptive.state["t"] + 1) % per_week
        await self.advance(per_week - done)

    async def live(self):
        """The live loop: one working hour every couple of seconds, with LLM judgement on."""
        idle = 0
        while self.running and not self.finished:
            await self.advance(1, use_llm=True)
            idle = 0 if self.listeners else idle + 1
            if idle > 20:  # nobody is watching: stop spending the free LLM tier
                break
            await asyncio.sleep(2.4 / self.speed)
        self.running = False
        self.publish()

    def play(self):
        if not self.running and not self.finished:
            self.running = True
            self.task = asyncio.create_task(self.live())

    def pause(self):
        self.running = False

    # ---- humans ------------------------------------------------------------

    def kill(self, agent):
        for world in self.worlds:
            world.kill(agent)

    def restore(self, agent):
        for world in self.worlds:
            world.restore(agent)

    def resolve(self, esc_id, choice):
        return self.adaptive.resolve(esc_id, choice, "human")

    # ---- views -------------------------------------------------------------

    def publish(self, reload=False):
        """Push to every open browser. `reload` tells them to refetch the full history."""
        events = [describe(e) for e in self.adaptive.fresh]
        points = {w.name: (w.state["history"][-1] if w.state["history"] else None) for w in self.worlds}
        for world in self.worlds:
            world.fresh.clear()
        msg = ({"kind": "reload"} if reload else
               {"kind": "tick", "events": events, "points": points, "state": self.summary()})
        for queue in list(self.listeners):
            if not queue.full():
                queue.put_nowait(msg)

    def summary(self):
        a, b = self.adaptive.state, self.baseline.state
        co = a["company"]

        def rows(coll, keys, n=14):
            return [{k: r.get(k) for k in keys} for r in list(a[coll].values())[-n:]][::-1]

        return {
            "clock": {"day": a["day"], "hour": a["hour"], "t": a["t"],
                      "week": (max(a["day"], 1) - 1) // C.DAYS_PER_WEEK + 1,
                      "time": f"{C.START_HOUR + a['hour']:02d}:00"},
            "running": self.running, "speed": self.speed, "finished": self.finished,
            "seed": self.seed,
            "kpi": {"adaptive": kpis(a), "baseline": kpis(b)},
            "company": {k: co[k] for k in ("capacity_level", "cash_level", "intake", "terms",
                                           "contractor_until", "budget_pending")},
            "agents": {
                name: {**a["agents"][name], "baseline_killed": b["agents"][name]["killed"],
                       "mailbox": len(self.adaptive.agents[name].mailbox)}
                for name in C.AGENTS},
            "inbox": [e for e in a["inbox"].values() if e["status"] == "open"],
            "decided": [e for e in a["inbox"].values() if e["status"] == "resolved"][-6:][::-1],
            "records": {
                "leads": rows("leads", ("id", "company", "city", "need", "value", "score", "status", "discount")),
                "customers": rows("customers", ("id", "name", "mrr", "health", "status"), 30),
                "work": rows("work", ("id", "name", "kind", "hours", "done", "due_day", "status")),
                "invoices": rows("invoices", ("id", "name", "amount", "due_day", "status", "reminders")),
                "tickets": rows("tickets", ("id", "name", "severity", "status", "text")),
            },
            "counts": {k: len(a[k]) for k in ("leads", "customers", "work", "invoices", "tickets")},
            "days_closed": sorted(int(d) for d in a["day_hashes"]),
            "llm": self.llm.stats() if self.llm else {"enabled": False, "calls": 0, "fallbacks": 0},
            "events_logged": self.adaptive.seq + self.baseline.seq,
        }

    def snapshot(self):
        return {
            "state": self.summary(),
            "history": {w.name: w.state["history"] for w in self.worlds},
            "feed": [describe(e) for e in self.adaptive.feed],
        }

    def replay(self, day):
        """Rebuild a day from the SQLite log alone and prove it matches what happened live."""
        log = self.store.read("adaptive", until_day=day)
        state, rebuilt = fold(log, until_day=day)
        recorded = self.adaptive.state["day_hashes"].get(str(day))
        base_state, _ = fold(self.store.read("baseline", until_day=day), until_day=day)
        return {
            "day": day, "events_folded": len(log), "rebuilt_hash": rebuilt, "recorded_hash": recorded,
            "verified": recorded is not None and rebuilt == recorded,
            "kpi": {"adaptive": kpis(state), "baseline": kpis(base_state)},
            "events": [describe(e) for e in log if e["day"] == day and e["type"] not in
                       ("agent.heartbeat", "kpi.snapshot", "work.progress", "clock.tick", "agent.note")],
        }
