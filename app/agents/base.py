"""What every role has in common: a mailbox, a charter, and no private path to state.

An agent reads the shared records, and changes them only by emitting events.
It learns what its colleagues did only from events delivered to its mailbox.
"""
from collections import deque
from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parents[2] / "company" / "roles"


class Agent:
    name = ""
    subscriptions = ()

    def __init__(self, world):
        self.w = world
        self.mailbox = deque()
        self.compensating = set()
        path = ROLES_DIR / f"{self.name}.md"
        self.charter = path.read_text(encoding="utf-8") if path.exists() else ""

    @property
    def s(self):
        return self.w.state

    @property
    def adaptive(self):
        return self.w.adaptive

    def emit(self, type_, **data):
        return self.w.emit(type_, self.name, **data)

    def drain(self, limit=None):
        out = []
        while self.mailbox and (limit is None or len(out) < limit):
            out.append(self.mailbox.popleft())
        return out

    def down(self, other):
        """True once the watchdog has told the company that a colleague is unresponsive."""
        return self.adaptive and self.s["agents"][other]["down"]

    def compensate(self, other, action):
        """Announce once per outage what this agent is doing to cover for a colleague."""
        if other not in self.compensating:
            self.compensating.add(other)
            self.emit("agent.compensating", agent=self.name, covering=other, action=action)

    def sync_outages(self):
        for other in list(self.compensating):
            if not self.down(other):
                self.compensating.discard(other)
                self.emit("agent.note", agent=self.name, note="")

    def has_escalation(self, ref):
        return any(e["ref"] == ref for e in self.s["inbox"].values())

    def escalate(self, kind, ref, title, detail, options, default, **extra):
        """Hand a decision to the human inbox. `default` is taken if nobody answers in time."""
        return self.w.escalate(self.name, kind, ref, title, detail, options, default, **extra)

    async def think(self, task, keys):
        """Optional LLM judgement. Returns None when the rule-based answer should stand."""
        if not self.w.use_llm or not self.w.llm:
            return None
        system = (self.charter + "\n\nReply with one JSON object only. Be brief and concrete.")
        return await self.w.llm.decide(system, task, keys)

    async def step(self):
        raise NotImplementedError
