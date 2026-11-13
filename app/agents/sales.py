from .. import config as C
from ..state import backlog_days
from .base import Agent


class Sales(Agent):
    name = "sales"
    subscriptions = ("escalation.resolved",)

    async def step(self):
        s, co = self.s, self.s["company"]
        self.sync_outages()

        for ev in self.drain():
            d = ev["data"]
            if d.get("kind") == "discount" and s["leads"][d["ref"]]["status"] == "awaiting_approval":
                lead = s["leads"][d["ref"]]
                self.close(lead, {"approve": lead["sens"], "counter": C.MAX_AUTO_DISCOUNT,
                                  "decline": 0}[d["choice"]], 100)

        if self.adaptive:
            self.adapt(co)

        budget = C.SALES_ACTIONS_PER_TICK
        for lead in [l for l in s["leads"].values() if l["status"] == "qualified"]:
            if not budget:
                break
            self.propose(lead)
            budget -= 1
        for lead in [l for l in s["leads"].values() if l["status"] == "new"]:
            if not budget:
                break
            await self.qualify(lead)
            budget -= 1

    def adapt(self, co):
        """Read the signals colleagues left on the shared record and change selling posture."""
        ops_down, fin_down = self.down("ops"), self.down("finance")
        intake = "throttled" if co["capacity_level"] == "red" or ops_down else "open"
        if intake != co["intake"]:
            reason = ("Ops is unresponsive, so no new start dates are promised" if ops_down else
                      "delivery backlog is red, so only strong leads are pursued at full price"
                      if intake == "throttled" else "delivery capacity recovered")
            self.emit("sales.mode_changed", intake=intake, reason=reason)
            if intake == "open":
                for lead in [l for l in self.s["leads"].values() if l["status"] == "parked"]:
                    self.emit("lead.unparked", id=lead["id"], company=lead["company"])
        terms = "deposit" if co["cash_level"] == "red" or fin_down else "normal"
        if terms != co["terms"]:
            reason = ("Finance is unresponsive, so no credit terms without a credit check" if fin_down
                      else "cash runway is short, so new deals pay the onboarding fee upfront"
                      if terms == "deposit" else "cash position recovered")
            self.emit("sales.terms_changed", terms=terms, reason=reason)
        if ops_down:
            self.compensate("ops", "Waitlisting weaker leads; no start dates promised")
        if fin_down:
            self.compensate("finance", "Deposit-only terms until credit checks are back")

    async def qualify(self, lead):
        score, pitch, brain = round(lead["fit"] * 100), f"Lead with {lead['need']}.", "rules"
        out = await self.think(
            f"Qualify this inbound lead for a B2B services firm.\nLead: {lead['note']}\n"
            f"Monthly retainer asked: AED {lead['value']}.\n"
            'Return {"score": 0-100 buying intent, "pitch": one sentence opening line}.',
            ("score", "pitch"))
        if out:
            try:
                score = max(0, min(100, round((score + int(out["score"])) / 2)))
                pitch, brain = str(out["pitch"])[:220], "llm"
            except (TypeError, ValueError):
                pass
        throttled = self.adaptive and self.s["company"]["intake"] == "throttled"
        if score < C.MIN_SCORE:
            self.emit("lead.disqualified", id=lead["id"], company=lead["company"], score=score,
                      brain=brain)
        elif throttled and (score < C.THROTTLED_MIN_SCORE
                            or backlog_days(self.s) >= C.BACKLOG_PAUSE_DAYS):
            self.emit("lead.qualified", id=lead["id"], company=lead["company"], score=score,
                      pitch=pitch, brain=brain)
            self.emit("lead.parked", id=lead["id"], company=lead["company"],
                      expires_day=self.s["day"] + 5)
        else:
            self.emit("lead.qualified", id=lead["id"], company=lead["company"], score=score,
                      pitch=pitch, brain=brain)

    def propose(self, lead):
        if not self.adaptive:
            return self.close(lead, C.BASELINE_DISCOUNT, 100)
        if self.s["company"]["intake"] == "throttled":
            if backlog_days(self.s) >= C.BACKLOG_PAUSE_DAYS:
                return self.emit("lead.parked", id=lead["id"], company=lead["company"],
                                 expires_day=self.s["day"] + 5)
            return self.close(lead, 0, 110)
        if lead["sens"] > C.MAX_AUTO_DISCOUNT and lead["value"] >= C.DISCOUNT_ESCALATION_VALUE:
            self.emit("lead.awaiting", id=lead["id"], company=lead["company"])
            return self.escalate(
                "discount", lead["id"],
                f"{lead['company']} wants {lead['sens']}% off an AED {lead['value']:,}/month retainer",
                f"My authority stops at {C.MAX_AUTO_DISCOUNT}%. Lead score {lead['score']}. "
                f"\"{lead['note']}\"",
                [{"key": "approve", "label": f"Approve {lead['sens']}%"},
                 {"key": "counter", "label": f"Counter at {C.MAX_AUTO_DISCOUNT}%"},
                 {"key": "decline", "label": "Hold list price"}], "counter")
        self.close(lead, min(lead["sens"], C.MAX_AUTO_DISCOUNT), 100)

    def close(self, lead, discount, price_pct):
        co = self.s["company"]
        deposit_terms = co["terms"] == "deposit"
        self.emit("proposal.sent", id=lead["id"], company=lead["company"], discount=discount,
                  price_pct=price_pct, terms=co["terms"])
        p = 0.28 + lead["score"] / 250
        p += 0.12 if discount >= lead["sens"] else -(lead["sens"] - discount) * 0.015
        p -= 0.06 if price_pct > 100 else 0
        p *= 0.85 if deposit_terms else 1
        if self.w.roll("close", lead["id"]) >= p:
            return self.emit("deal.lost", id=lead["id"], company=lead["company"])
        mrr = round(lead["value"] * (100 - discount) / 100 * price_pct / 100 / 100) * 100
        fee = mrr * C.ONBOARDING_FEE_PCT // 100
        cid = self.w.next_id("C", "customers")
        self.emit("deal.won", id=lead["id"], company=lead["company"], mrr=mrr, fee=fee,
                  deposit=fee if deposit_terms else 0,
                  start_delay=3 if co["intake"] == "throttled" else 0,
                  customer={"id": cid, "name": lead["company"], "status": "active", "mrr": mrr,
                            "health": 78, "cycle": self.s["day"] % C.DAYS_PER_WEEK,
                            "since": self.s["day"], "notified_day": 0, "last_outreach": 0,
                            "last_credit": 0})
