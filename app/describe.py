"""Turns an event into one plain sentence for the feed and the replay."""
from . import config as C


def _money(n):
    return f"AED {n:,}"


TEXT = {
    "world.seeded": lambda d: f"Company opened with market seed {d['seed']}.",
    "market.demand_spike": lambda d: f"Demand spike x{d['factor']}: {d['reason']}.",
    "lead.arrived": lambda d: f"New lead {d['id']}: {d['note']}",
    "lead.qualified": lambda d: f"Qualified {d['company']} at score {d['score']}. {d['pitch']}",
    "lead.disqualified": lambda d: f"Dropped {d['company']}: score {d['score']} is below the bar.",
    "lead.parked": lambda d: f"Waitlisted {d['company']} until delivery has room.",
    "lead.unparked": lambda d: f"Took {d['company']} off the waitlist.",
    "lead.held": lambda d: f"Sent {d['company']} a holding reply while Sales is down.",
    "lead.awaiting": lambda d: f"{d['company']} is waiting on a human discount decision.",
    "lead.expired": lambda d: f"{d['company']} went cold: nobody answered in time.",
    "proposal.sent": lambda d: (f"Proposal to {d['company']}: {d['discount']}% discount"
                                + (", premium price" if d["price_pct"] > 100 else "")
                                + (", deposit upfront" if d["terms"] == "deposit" else "") + "."),
    "deal.won": lambda d: f"Won {d['company']} at {_money(d['mrr'])}/month.",
    "deal.lost": lambda d: f"Lost {d['company']}.",
    "work.created": lambda d: f"{d['kind'].title()} job {d['id']} for {d['name']}: {d['hours']}h, due day {d['due_day']}.",
    "work.completed": lambda d: f"Delivered {d['kind']} job {d['id']} for {d['name']}" + (" (late)." if d["late"] else "."),
    "work.late": lambda d: f"Job {d['id']} for {d['name']} is late. Customer health -{d['penalty']}.",
    "invoice.issued": lambda d: f"Invoiced {d['name']} {_money(d['amount'])}, due day {d['due_day']}.",
    "invoice.overdue": lambda d: f"Invoice {d['id']} ({d['name']}, {_money(d['amount'])}) is overdue.",
    "invoice.reminded": lambda d: f"Reminder {d['n']} to {d['name']}: {d['message']}",
    "invoice.plan_offered": lambda d: f"Offered {d['name']} a payment plan on {d['id']}.",
    "invoice.written_off": lambda d: f"Wrote off {_money(d['amount'])} owed by {d['name']}.",
    "payment.received": lambda d: f"{d['customer']} paid {_money(d['amount'])}.",
    "ticket.opened": lambda d: f"{d['name']}: \"{d['text']}\"",
    "ticket.triaged": lambda d: f"Triaged {d['name']}'s ticket as {d['severity']}. Reply: {d['reply']}",
    "ticket.resolved": lambda d: f"Resolved {d['name']}'s ticket {d['id']}.",
    "support.fix_requested": lambda d: f"Asked Ops for an urgent fix for {d['name']}.",
    "support.outreach": lambda d: f"Checked in with {d['name']} (health {d['health']}).",
    "support.credit_issued": lambda d: f"Issued {d['name']} a goodwill credit of {_money(d['amount'])}.",
    "support.delay_notice": lambda d: f"Warned {d['name']} about a delivery delay.",
    "customer.churned": lambda d: f"{d['name']} cancelled. {_money(d['mrr'])}/month lost.",
    "ops.capacity_alert": lambda d: (f"Backlog is {d['backlog_days']} working days. Capacity alert raised."
                                     if d["level"] == "red" else
                                     f"Backlog back to {d['backlog_days']} days. Capacity alert cleared."),
    "ops.budget_requested": lambda d: f"Requested {_money(d['amount'])} for {d['hours']} contractor hours.",
    "finance.budget_approved": lambda d: f"Approved {_money(d['amount'])} of contractor spend.",
    "finance.budget_denied": lambda d: f"Contractor spend not approved: {d['reason']}.",
    "ops.contractor_engaged": lambda d: f"Contractor engaged for {d['days']} days, adding {d['extra']} delivery hours per working hour.",
    "sales.mode_changed": lambda d: f"Intake is now {d['intake']}: {d['reason']}.",
    "sales.terms_changed": lambda d: f"Payment terms are now {d['terms']}: {d['reason']}.",
    "finance.cash_alert": lambda d: (f"Cash runway is {d['runway']} days. Cash alert raised."
                                     if d["level"] == "red" else f"Runway back to {d['runway']} days."),
    "escalation.opened": lambda d: f"Escalated to a human: {d['title']}.",
    "escalation.resolved": lambda d: (f"{'Human chose' if d['by'] == 'human' else 'No answer in time, default applied:'}"
                                      f" \"{d['choice'].replace('_', ' ')}\" for: {d['title']}."),
    "agent.killed": lambda d: f"The {d['agent'].title()} agent was switched off.",
    "agent.restored": lambda d: f"The {d['agent'].title()} agent was restarted.",
    "watchdog.agent_down": lambda d: f"{d['agent'].title()} missed {d['missed']} heartbeats. All agents notified.",
    "watchdog.agent_recovered": lambda d: f"{d['agent'].title()} is back and draining its mailbox.",
    "agent.compensating": lambda d: f"Covering for {d['covering'].title()}: {d['action']}.",
    "day.closed": lambda d: f"Day {d['day']} closed. State hash {d['hash']}.",
    "costs.paid": lambda d: f"Paid {_money(d['fixed'] + d['contractor'])} in daily costs.",
    "ticket.aged": lambda d: "A ticket stayed open overnight.",
}

TONE = {
    "deal.won": "good", "payment.received": "good", "watchdog.agent_recovered": "good",
    "finance.budget_approved": "good", "ops.contractor_engaged": "good",
    "customer.churned": "bad", "work.late": "bad", "agent.killed": "bad",
    "watchdog.agent_down": "bad", "lead.expired": "bad", "invoice.written_off": "bad",
    "escalation.opened": "human", "escalation.resolved": "human",
    "ops.capacity_alert": "signal", "sales.mode_changed": "signal", "sales.terms_changed": "signal",
    "finance.cash_alert": "signal", "agent.compensating": "signal", "market.demand_spike": "signal",
    "ops.budget_requested": "signal",
}


def describe(ev):
    fn = TEXT.get(ev["type"])
    return {
        "seq": ev["seq"], "day": ev["day"], "time": f"{C.START_HOUR + ev['hour']:02d}:00",
        "type": ev["type"], "actor": ev["actor"], "tone": TONE.get(ev["type"], ""),
        "brain": ev["data"].get("brain"), "text": fn(ev["data"]) if fn else ev["type"],
    }
