from .. import config as C
from ..state import backlog_days, backlog_hours, capacity, open_work
from .base import Agent


class Ops(Agent):
    name = "ops"
    subscriptions = ("deal.won", "support.fix_requested", "finance.budget_approved",
                     "escalation.resolved")

    async def step(self):
        s, co = self.s, self.s["company"]
        self.sync_outages()

        for ev in self.drain():
            d = ev["data"]
            if ev["type"] == "deal.won":
                self.emit("work.created", id=self.w.next_id("W", "work"),
                          customer=d["customer"]["id"], name=d["company"], kind="onboarding",
                          hours=C.ONBOARDING_HOURS, bill=0 if d["deposit"] else d["fee"],
                          due_day=s["day"] + 3 + d["start_delay"], ticket=None)
            elif ev["type"] == "support.fix_requested":
                self.fix(d["ticket"], d["customer"], d["name"])
            elif ev["type"] == "finance.budget_approved":
                self.engage(d["extra"])
            elif d.get("kind") == "contractor":
                if d["choice"] == "approve":
                    self.engage(d["extra"])
                else:
                    self.emit("finance.budget_denied", reason="declined by a human")

        if self.down("support"):
            self.compensate("support", "Picking up untriaged tickets older than half a day")
            for t in [t for t in s["tickets"].values()
                      if t["status"] == "open" and s["t"] - t["opened_t"] >= 4]:
                self.emit("ticket.triaged", id=t["id"], name=t["name"], severity="high",
                          reply="Our delivery team has picked this up directly.", brain="rules")
                self.fix(t["id"], t["customer"], t["name"])

        self.deliver()
        if self.adaptive and s["hour"] == 0:
            self.watch_capacity(co)

    def fix(self, ticket, customer, name):
        self.emit("work.created", id=self.w.next_id("W", "work"), customer=customer, name=name,
                  kind="fix", hours=C.FIX_HOURS, bill=0, due_day=self.s["day"] + 1, ticket=ticket)

    def engage(self, extra):
        self.emit("ops.contractor_engaged", until_day=self.s["day"] + C.CONTRACTOR_DAYS,
                  extra=extra, days=C.CONTRACTOR_DAYS)

    def deliver(self):
        """Spend this hour's capacity. The adaptive company protects fixes and unhappy customers."""
        s, hours = self.s, capacity(self.s)
        queue = open_work(s)
        if self.adaptive:
            queue.sort(key=lambda w: (w["kind"] != "fix", w["due_day"],
                                      s["customers"][w["customer"]]["health"]))
        for work in queue:
            if not hours:
                break
            spend = min(hours, work["hours"] - work["done"])
            hours -= spend
            self.emit("work.progress", id=work["id"], hours=spend)
            if work["done"] >= work["hours"]:
                self.emit("work.completed", id=work["id"], customer=work["customer"],
                          name=work["name"], kind=work["kind"], bill=work["bill"],
                          ticket=work["ticket"], late=s["day"] > work["due_day"])

    def watch_capacity(self, co):
        days = backlog_days(self.s)
        if days >= C.BACKLOG_RED_DAYS and co["capacity_level"] != "red":
            self.emit("ops.capacity_alert", level="red", backlog_days=days)
        elif days <= C.BACKLOG_GREEN_DAYS and co["capacity_level"] == "red":
            self.emit("ops.capacity_alert", level="green", backlog_days=days)
        needs_help = (days >= C.BACKLOG_RED_DAYS and co["contractor_until"] < self.s["day"]
                      and not co["budget_pending"])
        if not needs_help:
            return
        per_step = C.CONTRACTOR_EXTRA * C.TICKS_PER_DAY * C.CONTRACTOR_DAYS * C.CONTRACTOR_RATE
        wanted = round(backlog_hours(self.s) / 40)
        affordable = co["cash"] * C.BUDGET_AUTHORITY_PCT // 100 // per_step
        steps = max(1, min(wanted, affordable, C.MAX_CONTRACTOR_EXTRA // C.CONTRACTOR_EXTRA))
        extra = steps * C.CONTRACTOR_EXTRA
        hours = extra * C.TICKS_PER_DAY * C.CONTRACTOR_DAYS
        amount = hours * C.CONTRACTOR_RATE
        self.emit("ops.budget_requested", amount=amount, hours=hours, extra=extra, backlog_days=days)
        if self.down("finance"):
            self.compensate("finance", "Sending contractor spend straight to the human inbox")
            self.escalate(
                "contractor", f"contractor@{self.s['t']}",
                f"Approve AED {amount:,} for a contractor? Finance is down",
                f"Backlog is {days} working days. {hours} contractor hours would clear it. "
                "Finance normally approves this but is not responding.",
                [{"key": "approve", "label": "Approve spend"}, {"key": "deny", "label": "Deny"}],
                "deny", extra=extra)
