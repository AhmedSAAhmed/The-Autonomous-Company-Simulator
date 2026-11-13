"""The shared record system.

State is a plain JSON-serialisable dict. It is only ever changed by applying an
event through a reducer in this file, so folding the event log from the first
event always rebuilds the exact same company. That is what makes a day replayable.
"""
import hashlib
import json

from . import config as C

REDUCERS = {}


def on(event_type):
    def register(fn):
        REDUCERS[event_type] = fn
        return fn
    return register


def roll(seed, *key):
    """Deterministic pseudo-random number in [0, 1) for a named decision."""
    raw = "|".join(str(k) for k in (seed,) + key).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") / 2**64


SEED_CUSTOMERS = [
    "Al Noor Logistics", "Gulf Bridge Trading", "Sadaf Clinics", "Najm Contracting",
    "Bayan Retail Group", "Marsa Hospitality", "Tamkeen Foods", "Wahat Tech",
]


def initial_state(seed):
    customers = {}
    for i, name in enumerate(SEED_CUSTOMERS):
        cid = f"C{i + 1:03d}"
        customers[cid] = {
            "id": cid, "name": name, "status": "active",
            "mrr": 5_000 + int(roll(seed, "mrr", i) * 6) * 1_000,
            "health": 70 + int(roll(seed, "health", i) * 19),
            "cycle": i % C.DAYS_PER_WEEK, "since": 0,
            "notified_day": 0, "last_outreach": 0, "last_credit": 0,
        }
    return {
        "seed": seed, "day": 0, "hour": 0, "t": -1,
        "company": {
            "cash": C.START_CASH, "revenue": 0, "cost": 0, "bad_debt": 0,
            "capacity_level": "green", "cash_level": "green",
            "intake": "open", "terms": "normal",
            "contractor_until": 0, "contractor_extra": 0, "budget_pending": False,
            "on_time": 0, "late": 0,
        },
        "leads": {}, "customers": customers, "work": {}, "invoices": {},
        "tickets": {}, "inbox": {},
        "agents": {a: {"last_beat": -1, "killed": False, "down": False, "note": "", "actions": 0}
                   for a in C.AGENTS},
        "history": [], "day_hashes": {},
    }


def state_hash(state):
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def apply(state, ev):
    fn = REDUCERS.get(ev["type"])
    if fn:
        fn(state, ev["data"])


def fold(events, until_day=None):
    """Rebuild state from an event log. Returns (state, hash_at_close_of_until_day)."""
    state = None
    for ev in events:
        if ev["type"] == "world.seeded":
            state = initial_state(ev["data"]["seed"])
            continue
        if until_day is not None and ev["type"] == "day.closed" and ev["data"]["day"] == until_day:
            return state, state_hash(state)
        apply(state, ev)
    return state, state_hash(state) if state else None


def open_work(state):
    return [w for w in state["work"].values() if w["status"] in ("queued", "in_progress")]


def capacity(state):
    co = state["company"]
    extra = co["contractor_extra"] if co["contractor_until"] >= state["day"] else 0
    return C.BASE_CAPACITY + extra


def backlog_hours(state):
    return sum(w["hours"] - w["done"] for w in open_work(state))


def backlog_days(state):
    return round(backlog_hours(state) / (capacity(state) * C.TICKS_PER_DAY), 2)


def kpis(state):
    co = state["company"]
    active = [c for c in state["customers"].values() if c["status"] == "active"]
    churned = sum(1 for c in state["customers"].values() if c["status"] == "churned")
    open_inv = [i for i in state["invoices"].values() if i["status"] in ("sent", "overdue")]
    delivered = co["on_time"] + co["late"]
    return {
        "day": state["day"], "hour": state["hour"], "t": state["t"],
        "cash": co["cash"], "revenue": co["revenue"], "cost": co["cost"],
        "profit": co["revenue"] - co["cost"] - co["bad_debt"],
        "mrr": sum(c["mrr"] for c in active), "customers": len(active), "churned": churned,
        "backlog_hours": backlog_hours(state), "backlog_days": backlog_days(state),
        "ar_open": sum(i["amount"] for i in open_inv),
        "ar_overdue": sum(i["amount"] for i in open_inv if i["status"] == "overdue"),
        "on_time_pct": round(100 * co["on_time"] / delivered) if delivered else 100,
        "open_escalations": sum(1 for e in state["inbox"].values() if e["status"] == "open"),
        "deals_won": sum(1 for l in state["leads"].values() if l["status"] == "won"),
        "leads_lost": sum(1 for l in state["leads"].values() if l["status"] in ("lost", "expired")),
    }


def _health(customer, delta):
    customer["health"] = max(0, min(100, customer["health"] + delta))


# ---- clock -----------------------------------------------------------------

@on("clock.tick")
def _tick(s, d):
    s["day"], s["hour"], s["t"] = d["day"], d["hour"], d["t"]


@on("kpi.snapshot")
def _kpi(s, d):
    s["history"].append(d)


@on("day.closed")
def _day_closed(s, d):
    s["day_hashes"][str(d["day"])] = d["hash"]


# ---- leads and deals -------------------------------------------------------

@on("lead.arrived")
def _lead_arrived(s, d):
    s["leads"][d["id"]] = {**d, "status": "new", "score": None, "discount": None,
                           "expires_day": d["day"] + C.LEAD_TTL_DAYS, "held": False}


@on("lead.qualified")
def _lead_qualified(s, d):
    s["leads"][d["id"]].update(status="qualified", score=d["score"], pitch=d["pitch"])


@on("lead.disqualified")
def _lead_disqualified(s, d):
    s["leads"][d["id"]].update(status="lost", score=d["score"])


@on("lead.parked")
def _lead_parked(s, d):
    s["leads"][d["id"]].update(status="parked", expires_day=d["expires_day"])


@on("lead.unparked")
def _lead_unparked(s, d):
    s["leads"][d["id"]]["status"] = "qualified"


@on("lead.held")
def _lead_held(s, d):
    s["leads"][d["id"]].update(held=True, expires_day=d["expires_day"])


@on("lead.awaiting")
def _lead_awaiting(s, d):
    s["leads"][d["id"]]["status"] = "awaiting_approval"


@on("lead.expired")
def _lead_expired(s, d):
    s["leads"][d["id"]]["status"] = "expired"


@on("proposal.sent")
def _proposal(s, d):
    s["leads"][d["id"]]["discount"] = d["discount"]


@on("deal.lost")
def _deal_lost(s, d):
    s["leads"][d["id"]]["status"] = "lost"


@on("deal.won")
def _deal_won(s, d):
    s["leads"][d["id"]]["status"] = "won"
    s["customers"][d["customer"]["id"]] = dict(d["customer"])
    if d["deposit"]:
        s["company"]["cash"] += d["deposit"]
        s["company"]["revenue"] += d["deposit"]


# ---- work ------------------------------------------------------------------

@on("work.created")
def _work_created(s, d):
    s["work"][d["id"]] = {**d, "done": 0, "status": "queued", "created_day": s["day"],
                          "late_days": 0}


@on("work.progress")
def _work_progress(s, d):
    w = s["work"][d["id"]]
    w["done"] += d["hours"]
    w["status"] = "in_progress"


@on("work.completed")
def _work_completed(s, d):
    w = s["work"][d["id"]]
    w.update(status="done", done_day=s["day"])
    s["company"]["late" if d["late"] else "on_time"] += 1
    if not d["late"] and w["customer"] in s["customers"]:
        _health(s["customers"][w["customer"]], 2)


@on("work.late")
def _work_late(s, d):
    s["work"][d["id"]]["late_days"] += 1
    _health(s["customers"][d["customer"]], -d["penalty"])


# ---- invoices and cash -----------------------------------------------------

@on("invoice.issued")
def _invoice_issued(s, d):
    s["invoices"][d["id"]] = {**d, "status": "sent", "issued_day": s["day"],
                              "reminders": 0, "plan": False}
    s["company"]["revenue"] += d["amount"]


@on("invoice.overdue")
def _invoice_overdue(s, d):
    s["invoices"][d["id"]]["status"] = "overdue"


@on("invoice.reminded")
def _invoice_reminded(s, d):
    s["invoices"][d["id"]]["reminders"] = d["n"]


@on("invoice.plan_offered")
def _invoice_plan(s, d):
    s["invoices"][d["id"]]["plan"] = True


@on("invoice.written_off")
def _invoice_written_off(s, d):
    inv = s["invoices"][d["id"]]
    inv["status"] = "written_off"
    s["company"]["bad_debt"] += inv["amount"]


@on("payment.received")
def _payment(s, d):
    s["invoices"][d["id"]]["status"] = "paid"
    s["company"]["cash"] += d["amount"]


@on("costs.paid")
def _costs(s, d):
    total = d["fixed"] + d["contractor"]
    s["company"]["cash"] -= total
    s["company"]["cost"] += total


# ---- support ---------------------------------------------------------------

@on("ticket.opened")
def _ticket_opened(s, d):
    s["tickets"][d["id"]] = {**d, "status": "open", "severity": None, "opened_t": s["t"]}


@on("ticket.triaged")
def _ticket_triaged(s, d):
    s["tickets"][d["id"]].update(status="triaged", severity=d["severity"], reply=d["reply"])


@on("ticket.resolved")
def _ticket_resolved(s, d):
    t = s["tickets"][d["id"]]
    t["status"] = "resolved"
    _health(s["customers"][t["customer"]], 3)


@on("ticket.aged")
def _ticket_aged(s, d):
    _health(s["customers"][d["customer"]], -C.TICKET_AGE_PENALTY)


@on("support.outreach")
def _outreach(s, d):
    c = s["customers"][d["customer"]]
    c["last_outreach"] = s["day"]
    _health(c, 6)


@on("support.credit_issued")
def _credit(s, d):
    c = s["customers"][d["customer"]]
    c["last_credit"] = s["day"]
    _health(c, 12)
    s["company"]["cash"] -= d["amount"]
    s["company"]["cost"] += d["amount"]


@on("support.delay_notice")
def _delay_notice(s, d):
    s["customers"][d["customer"]]["notified_day"] = s["day"]


@on("customer.churned")
def _churned(s, d):
    s["customers"][d["id"]]["status"] = "churned"
    for w in open_work(s):
        if w["customer"] == d["id"]:
            w["status"] = "cancelled"


# ---- cross-agent signals ---------------------------------------------------

@on("ops.capacity_alert")
def _capacity_alert(s, d):
    s["company"]["capacity_level"] = d["level"]


@on("ops.budget_requested")
def _budget_requested(s, d):
    s["company"]["budget_pending"] = True


@on("finance.budget_denied")
def _budget_denied(s, d):
    s["company"]["budget_pending"] = False


@on("ops.contractor_engaged")
def _contractor(s, d):
    s["company"].update(contractor_until=d["until_day"], contractor_extra=d["extra"],
                        budget_pending=False)


@on("sales.mode_changed")
def _sales_mode(s, d):
    s["company"]["intake"] = d["intake"]


@on("sales.terms_changed")
def _sales_terms(s, d):
    s["company"]["terms"] = d["terms"]


@on("finance.cash_alert")
def _cash_alert(s, d):
    s["company"]["cash_level"] = d["level"]


# ---- humans and the control plane ------------------------------------------

@on("escalation.opened")
def _esc_opened(s, d):
    s["inbox"][d["id"]] = {**d, "status": "open", "opened_t": s["t"], "choice": None, "by": None}


@on("escalation.resolved")
def _esc_resolved(s, d):
    s["inbox"][d["id"]].update(status="resolved", choice=d["choice"], by=d["by"])


@on("agent.heartbeat")
def _heartbeat(s, d):
    a = s["agents"][d["agent"]]
    a["last_beat"] = s["t"]
    a["actions"] += d["actions"]


@on("agent.killed")
def _killed(s, d):
    s["agents"][d["agent"]]["killed"] = True


@on("agent.restored")
def _restored(s, d):
    s["agents"][d["agent"]]["killed"] = False


@on("watchdog.agent_down")
def _down(s, d):
    s["agents"][d["agent"]]["down"] = True


@on("watchdog.agent_recovered")
def _recovered(s, d):
    s["agents"][d["agent"]]["down"] = False


@on("agent.compensating")
def _compensating(s, d):
    s["agents"][d["agent"]]["note"] = d["action"]


@on("agent.note")
def _note(s, d):
    s["agents"][d["agent"]]["note"] = d["note"]
