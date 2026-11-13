from .. import config as C
from .base import Agent


class Finance(Agent):
    name = "finance"
    subscriptions = ("work.completed", "ops.budget_requested", "escalation.resolved")

    async def step(self):
        s, co = self.s, self.s["company"]
        self.sync_outages()
        runway = co["cash"] / C.DAILY_FIXED_COST

        for ev in self.drain():
            d = ev["data"]
            if ev["type"] == "work.completed" and d["bill"]:
                self.emit("invoice.issued", id=self.w.next_id("I", "invoices"),
                          customer=d["customer"], name=d["name"], amount=d["bill"], work=d["id"],
                          due_day=s["day"] + C.INVOICE_TERMS_DAYS)
            elif ev["type"] == "ops.budget_requested" and co["budget_pending"]:
                self.review_budget(d, runway)
            elif d.get("kind") == "budget":
                if d["choice"] == "approve":
                    self.emit("finance.budget_approved", amount=d["amount"], extra=d["extra"],
                              by="human")
                else:
                    self.emit("finance.budget_denied", reason="declined by a human")
            elif d.get("kind") == "overdue":
                if d["choice"] == "write_off":
                    self.emit("invoice.written_off", id=d["ref"], name=s["invoices"][d["ref"]]["name"],
                              amount=s["invoices"][d["ref"]]["amount"])
                elif d["choice"] == "plan":
                    self.emit("invoice.plan_offered", id=d["ref"],
                              name=s["invoices"][d["ref"]]["name"])

        if s["hour"] != 0:
            return
        for inv in [i for i in s["invoices"].values() if i["status"] == "sent"]:
            if s["day"] > inv["due_day"]:
                self.emit("invoice.overdue", id=inv["id"], name=inv["name"], amount=inv["amount"])
        await self.chase()
        if self.adaptive:
            if runway < C.RUNWAY_RED_DAYS and co["cash_level"] != "red":
                self.emit("finance.cash_alert", level="red", runway=round(runway, 1))
            elif runway > C.RUNWAY_GREEN_DAYS and co["cash_level"] == "red":
                self.emit("finance.cash_alert", level="green", runway=round(runway, 1))

    def review_budget(self, d, runway):
        authority = self.s["company"]["cash"] * C.BUDGET_AUTHORITY_PCT // 100
        if d["amount"] <= authority and runway >= C.BUDGET_MIN_RUNWAY:
            return self.emit("finance.budget_approved", amount=d["amount"], extra=d["extra"],
                             by="finance", runway=round(runway, 1))
        self.escalate(
            "budget", f"budget@{self.s['t']}",
            f"Ops asks AED {d['amount']:,} for a contractor with {runway:.0f} days of runway",
            f"Backlog is {d['backlog_days']} working days. I approve spend up to "
            f"{C.BUDGET_AUTHORITY_PCT}% of cash (AED {authority:,}) and only when runway is "
            f"{C.BUDGET_MIN_RUNWAY} days or more.",
            [{"key": "approve", "label": "Approve spend"}, {"key": "deny", "label": "Deny"}], "deny",
            amount=d["amount"], extra=d["extra"])

    async def chase(self):
        s = self.s
        for inv in [i for i in s["invoices"].values() if i["status"] == "overdue"]:
            over = s["day"] - inv["due_day"]
            limit = 2 if self.adaptive else 1
            if inv["reminders"] < min(over, limit):
                message, brain = (f"Friendly reminder: invoice {inv['id']} for AED "
                                  f"{inv['amount']:,} is {over} day(s) past due."), "rules"
                out = await self.think(
                    f"Write a short, courteous payment reminder to {inv['name']} for invoice "
                    f"{inv['id']}, AED {inv['amount']}, {over} day(s) overdue. Reminder number "
                    f"{inv['reminders'] + 1}. Return {{\"message\": \"...\"}}.", ("message",))
                if out:
                    message, brain = str(out["message"])[:320], "llm"
                self.emit("invoice.reminded", id=inv["id"], name=inv["name"],
                          n=inv["reminders"] + 1, message=message, brain=brain)
            elif self.adaptive and over >= 3 and not inv["plan"] and not self.has_escalation(inv["id"]):
                if inv["amount"] <= C.PLAN_AUTHORITY:
                    self.emit("invoice.plan_offered", id=inv["id"], name=inv["name"])
                else:
                    self.escalate(
                        "overdue", inv["id"],
                        f"{inv['name']} owes AED {inv['amount']:,}, {over} days overdue",
                        f"Two reminders sent with no payment. I can offer payment plans up to AED "
                        f"{C.PLAN_AUTHORITY:,} on my own; this one is above that.",
                        [{"key": "plan", "label": "Offer payment plan"},
                         {"key": "write_off", "label": "Write it off"},
                         {"key": "wait", "label": "Keep chasing"}], "plan")
