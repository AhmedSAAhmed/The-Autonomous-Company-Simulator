"""One company: shared state, the event bus, its agents and the watchdog."""
from collections import deque

from . import config as C
from . import market
from .agents import ROSTER
from .state import apply, initial_state, kpis, roll, state_hash

QUIET = {"clock.tick", "agent.heartbeat", "kpi.snapshot", "work.progress", "day.closed",
         "costs.paid", "agent.note", "ticket.aged"}


class World:
    def __init__(self, name, seed, adaptive, store, llm=None):
        self.name, self.seed, self.adaptive = name, seed, adaptive
        self.store, self.llm = store, llm
        self.use_llm = False
        self.seq = 0
        self.killed = set()
        self.fresh = []  # events emitted since the last publish
        self.feed = deque(maxlen=150)
        self.on_restart = None
        self.state = initial_state(seed)
        self.agents = {cls.name: cls(self) for cls in ROSTER}
        self.emit("world.seeded", "clock", seed=seed, adaptive=adaptive)

    # ---- the bus -----------------------------------------------------------

    def emit(self, type_, actor, **data):
        self.seq += 1
        ev = {"world": self.name, "seq": self.seq, "type": type_, "actor": actor, "data": data}
        apply(self.state, ev)
        ev.update(day=self.state["day"], hour=self.state["hour"], t=self.state["t"])
        self.store.append(ev)
        for agent in self.agents.values():
            if type_ in agent.subscriptions:
                agent.mailbox.append(ev)
        if type_ not in QUIET:
            self.fresh.append(ev)
            self.feed.append(ev)
        return ev

    def roll(self, *key):
        return roll(self.seed, *key)

    def next_id(self, prefix, collection):
        return f"{prefix}{len(self.state[collection]) + 1:03d}"

    # ---- humans ------------------------------------------------------------

    def escalate(self, agent, kind, ref, title, detail, options, default, **extra):
        return self.emit("escalation.opened", agent, id=self.next_id("E", "inbox"), agent=agent,
                         kind=kind, ref=ref, title=title, detail=detail, options=options,
                         default=default, extra=extra,
                         expires_t=self.state["t"] + C.ESCALATION_TTL_TICKS)

    def resolve(self, esc_id, choice, by):
        esc = self.state["inbox"].get(esc_id)
        if not esc or esc["status"] != "open" or choice not in [o["key"] for o in esc["options"]]:
            return False
        self.emit("escalation.resolved", by, id=esc_id, agent=esc["agent"], kind=esc["kind"],
                  ref=esc["ref"], title=esc["title"], choice=choice, by=by, **esc["extra"])
        if esc["kind"] == "agent_down" and choice == "restart" and self.on_restart:
            self.on_restart(esc["ref"].split("@")[0])
        return True

    def kill(self, agent):
        if agent in self.agents and agent not in self.killed:
            self.killed.add(agent)
            self.emit("agent.killed", "human", agent=agent)

    def restore(self, agent):
        if agent in self.killed:
            self.killed.discard(agent)
            self.emit("agent.restored", "human", agent=agent)

    # ---- the control plane -------------------------------------------------

    def watchdog(self):
        """Notice a silent agent, tell the company, and page a human."""
        s = self.state
        for name, a in s["agents"].items():
            missed = s["t"] - a["last_beat"]
            if missed >= C.HEARTBEAT_MISSES and not a["down"]:
                self.emit("watchdog.agent_down", "watchdog", agent=name, missed=missed)
                self.escalate(
                    "watchdog", "agent_down", f"{name}@{s['t']}",
                    f"The {name.title()} agent has stopped responding",
                    f"It missed {missed} heartbeats. The other agents have been told and are "
                    "covering what they can. Its mailbox keeps every event it misses and is "
                    "processed when it returns.",
                    [{"key": "restart", "label": "Restart agent"},
                     {"key": "keep_down", "label": "Keep it down"}], "restart")
            elif missed == 0 and a["down"]:
                self.emit("watchdog.agent_recovered", "watchdog", agent=name,
                          drained=a["actions"])

    def timeouts(self):
        for esc in list(self.state["inbox"].values()):
            if esc["status"] == "open" and self.state["t"] >= esc["expires_t"]:
                self.resolve(esc["id"], esc["default"], "timeout")

    # ---- one working hour --------------------------------------------------

    async def tick(self, use_llm=False):
        s = self.state
        t = s["t"] + 1
        self.use_llm = use_llm and self.adaptive
        self.emit("clock.tick", "clock", day=t // C.TICKS_PER_DAY + 1, hour=t % C.TICKS_PER_DAY, t=t)
        if s["hour"] == 0:
            market.day_start(self)
        market.arrivals(self)
        for agent in self.agents.values():
            if agent.name in self.killed:
                continue
            before = self.seq
            await agent.step()
            self.emit("agent.heartbeat", agent.name, agent=agent.name, actions=self.seq - before)
        if self.adaptive:
            self.watchdog()
            self.timeouts()
        last_hour = s["hour"] == C.TICKS_PER_DAY - 1
        if last_hour:
            market.day_end(self)
        self.emit("kpi.snapshot", "clock", **kpis(s))
        if last_hour:
            self.emit("day.closed", "clock", day=s["day"], hash=state_hash(s))
        self.store.commit()
