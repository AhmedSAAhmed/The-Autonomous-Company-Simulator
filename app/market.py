"""The outside world: a stream of events the company does not control.

Everything here is a pure function of (seed, day, entity), so the adaptive company
and the static baseline face exactly the same market.
"""
from . import config as C
from .state import open_work

COMPANIES = [
    "Dana Freight", "Qasr Interiors", "Lulu Dental", "Shams Solar", "Barq Couriers", "Jood Catering",
    "Rawabi Schools", "Misk Perfumes", "Sahab Cloud", "Nakheel Farms", "Zad Pharmacy", "Burj Fitness",
    "Yusr Fintech", "Reef Marine", "Fajr Media", "Wadi Motors", "Hilal Print", "Thuraya Travel",
    "Oud Living", "Karam Bakeries", "Safa Labs", "Noor Optics", "Asas Engineering", "Madar Events",
]
CITIES = ["Riyadh", "Dubai", "Manama", "Doha", "Jeddah", "Kuwait City", "Muscat", "Abu Dhabi"]
NEEDS = ["payroll and WPS compliance", "bookkeeping and VAT filing", "company setup and licensing",
         "HR outsourcing", "PRO and visa processing"]
HOT = ["Budget is approved and we need to start this month.",
       "Our current provider missed two filings; we want to switch now.",
       "The board signed off, we just need a proposal and a start date."]
WARM = ["We are comparing three providers and price matters.",
        "Interested, but we need to see how onboarding would work first.",
        "We could move next quarter if the numbers make sense."]
COLD = ["Just exploring options, no budget assigned yet.",
        "Please send a brochure, we may revisit next year.",
        "A student project, collecting quotes for a case study."]
TICKETS_HIGH = ["Our payroll file was rejected by the bank and salaries are due tomorrow.",
                "The VAT return shows the wrong figures and the deadline is this week.",
                "A visa renewal was missed and we now face a penalty."]
TICKETS_LOW = ["Can you resend last month's management report?",
               "How do we add a new employee to the portal?",
               "Please update our billing contact."]


def _pick(w, items, *key):
    return items[int(w.roll(*key) * len(items))]


def day_start(w):
    s, day = w.state, w.state["day"]
    co = s["company"]

    if day in C.DEMAND_SPIKES and (day - 1) not in C.DEMAND_SPIKES:
        w.emit("market.demand_spike", "market", factor=C.DEMAND_SPIKES[day],
               reason="regional VAT filing deadline is driving inbound demand")

    contractor = 0
    if co["contractor_until"] >= day:
        contractor = co["contractor_extra"] * C.TICKS_PER_DAY * C.CONTRACTOR_RATE
    w.emit("costs.paid", "bank", fixed=C.DAILY_FIXED_COST, contractor=contractor)

    for inv in list(s["invoices"].values()):
        if inv["status"] not in ("sent", "overdue") or inv["issued_day"] >= day:
            continue
        cust = s["customers"][inv["customer"]]
        p = 0.15 if cust["status"] == "churned" else (0.5 if cust["health"] >= 50 else 0.25)
        p = min(0.95, p + 0.15 * inv["reminders"] + (0.25 if inv["plan"] else 0))
        if w.roll("pay", inv["id"], day) < p:
            w.emit("payment.received", "customer", id=inv["id"], amount=inv["amount"],
                   customer=cust["name"])

    for cust in list(s["customers"].values()):
        if cust["status"] != "active":
            continue
        if (day - 1) % C.DAYS_PER_WEEK == cust["cycle"] and cust["since"] < day:
            w.emit("work.created", "contract", id=w.next_id("W", "work"), customer=cust["id"],
                   name=cust["name"], kind="service", hours=max(2, round(cust["mrr"] / 1000)),
                   bill=cust["mrr"] // 4, due_day=day + 2, ticket=None)
        late = any(x["customer"] == cust["id"] and x["late_days"] for x in open_work(s))
        if w.roll("ticket", cust["id"], day) < 0.06 + (0.22 if late else 0):
            high = late or w.roll("ticket-sev", cust["id"], day) < 0.3
            text = _pick(w, TICKETS_HIGH if high else TICKETS_LOW, "ticket-text", cust["id"], day)
            w.emit("ticket.opened", "customer", id=w.next_id("T", "tickets"), customer=cust["id"],
                   name=cust["name"], text=text, true_sev="high" if high else "low", day=day)

    for lead in list(s["leads"].values()):
        if lead["status"] in ("new", "qualified", "parked") and lead["expires_day"] < day:
            w.emit("lead.expired", "market", id=lead["id"], company=lead["company"])


def arrivals(w):
    """Leads land throughout the day, at the hour the market decides."""
    s, day, hour = w.state, w.state["day"], w.state["hour"]
    n = round((2 + int(w.roll("leads", day) * 3)) * C.DEMAND_SPIKES.get(day, 1))
    for i in range(n):
        if int(w.roll("lead-hour", day, i) * C.TICKS_PER_DAY) != hour:
            continue
        fit = round(0.2 + 0.75 * w.roll("fit", day, i), 2)
        notes = HOT if fit > 0.7 else WARM if fit > 0.45 else COLD
        r = w.roll("sens", day, i)
        sens = 0 if r < 0.5 else 10 if r < 0.75 else 20 if r < 0.9 else 25
        need = _pick(w, NEEDS, "need", day, i)
        city = _pick(w, CITIES, "city", day, i)
        company = _pick(w, COMPANIES, "co", day, i)
        w.emit("lead.arrived", "market", id=w.next_id("L", "leads"), company=company, city=city,
               need=need, value=3_000 + int(w.roll("value", day, i) * 10) * 1_000, fit=fit, sens=sens,
               note=f"{company}, {city}. Looking for {need}. {_pick(w, notes, 'note', day, i)}",
               day=day)


def day_end(w):
    s, day = w.state, w.state["day"]
    for work in open_work(s):
        cust = s["customers"].get(work["customer"])
        if work["due_day"] <= day and cust and cust["status"] == "active":
            notified = cust["notified_day"] >= day - 1
            w.emit("work.late", "clock", id=work["id"], customer=cust["id"], name=cust["name"],
                   penalty=C.LATE_PENALTY_NOTIFIED if notified else C.LATE_PENALTY)
    for ticket in list(s["tickets"].values()):
        if ticket["status"] != "resolved" and ticket["day"] < day:
            if s["customers"][ticket["customer"]]["status"] == "active":
                w.emit("ticket.aged", "clock", id=ticket["id"], customer=ticket["customer"])
    for cust in list(s["customers"].values()):
        if cust["status"] == "active" and cust["health"] < C.CHURN_HEALTH:
            if w.roll("churn", cust["id"], day) < C.CHURN_PROB:
                w.emit("customer.churned", "customer", id=cust["id"], name=cust["name"],
                       mrr=cust["mrr"])
