import re

from .. import config as C
from ..state import open_work
from .base import Agent

URGENT = re.compile(r"reject|wrong|deadline|missed|penalt|due tomorrow", re.I)


class Support(Agent):
    name = "support"
    subscriptions = ("ticket.opened", "work.completed", "escalation.resolved")

    async def step(self):
        s = self.s
        self.sync_outages()

        handled, later = 0, []
        for ev in self.drain():
            d = ev["data"]
            if ev["type"] == "ticket.opened":
                if handled >= C.TICKETS_PER_TICK:
                    later.append(ev)
                elif s["tickets"][d["id"]]["status"] == "open":
                    await self.triage(s["tickets"][d["id"]])
                    handled += 1
            elif ev["type"] == "work.completed" and d["ticket"]:
                if s["tickets"][d["ticket"]]["status"] != "resolved":
                    self.emit("ticket.resolved", id=d["ticket"], name=d["name"])
            elif d.get("kind") == "credit" and d["choice"] == "approve":
                self.emit("support.credit_issued", customer=d["ref"], amount=d["amount"],
                          name=s["customers"][d["ref"]]["name"], by="human")
        self.mailbox.extendleft(reversed(later))

        if not self.adaptive:
            return
        if s["hour"] == 1:
            self.retain()
        if self.down("ops"):
            self.compensate("ops", "Warning customers about delays before they ask")
            for cid in {w["customer"] for w in open_work(s) if w["due_day"] <= s["day"] + 1}:
                cust = s["customers"][cid]
                if cust["status"] == "active" and cust["notified_day"] < s["day"]:
                    self.emit("support.delay_notice", customer=cid, name=cust["name"])
        if self.down("sales"):
            self.compensate("sales", "Sending holding replies so new leads do not go cold")
            for lead in [l for l in s["leads"].values() if l["status"] == "new" and not l["held"]]:
                self.emit("lead.held", id=lead["id"], company=lead["company"],
                          expires_day=lead["expires_day"] + 2)

    async def triage(self, ticket):
        severity = "high" if URGENT.search(ticket["text"]) else "low"
        reply, brain = ("We are on it and will confirm the fix today." if severity == "high"
                        else "Done, details are in your portal."), "rules"
        out = await self.think(
            f"Customer {ticket['name']} wrote: \"{ticket['text']}\"\n"
            'Return {"severity": "high" or "low", "reply": one or two sentences}. '
            "High means money, deadlines or compliance are at risk.", ("severity", "reply"))
        if out and out["severity"] in ("high", "low"):
            severity, reply, brain = out["severity"], str(out["reply"])[:320], "llm"
        self.emit("ticket.triaged", id=ticket["id"], name=ticket["name"], severity=severity,
                  reply=reply, brain=brain)
        if severity == "high":
            self.emit("support.fix_requested", ticket=ticket["id"], customer=ticket["customer"],
                      name=ticket["name"])
        else:
            self.emit("ticket.resolved", id=ticket["id"], name=ticket["name"])

    def retain(self):
        """Once a day, reach out to unhappy customers before they leave."""
        s, day = self.s, self.s["day"]
        for cust in [c for c in s["customers"].values() if c["status"] == "active"]:
            if cust["health"] < C.OUTREACH_HEALTH and cust["last_outreach"] < day - 1:
                self.emit("support.outreach", customer=cust["id"], name=cust["name"],
                          health=cust["health"])
            if cust["health"] >= C.CREDIT_HEALTH or cust["last_credit"] > day - 5:
                continue
            amount = round(cust["mrr"] * 0.08 / 50) * 50
            ref = cust["id"]
            if self.down("finance"):
                self.compensate("finance", "Holding goodwill credits until Finance is back")
            elif amount <= C.CREDIT_AUTHORITY:
                self.emit("support.credit_issued", customer=ref, amount=amount, name=cust["name"],
                          by="support")
            elif not any(e["ref"] == ref and e["status"] == "open" for e in s["inbox"].values()):
                self.escalate(
                    "credit", ref,
                    f"Goodwill credit of AED {amount:,} for {cust['name']}?",
                    f"Health is {cust['health']}/100 after late delivery. I can issue credits up "
                    f"to AED {C.CREDIT_AUTHORITY} myself. They pay AED {cust['mrr']:,} a month.",
                    [{"key": "approve", "label": "Issue credit"},
                     {"key": "decline", "label": "No credit"}], "decline", amount=amount)
