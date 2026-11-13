"""The claims the demo makes, checked without a browser or an LLM."""
import asyncio

from app import config as C
from app.engine import Simulation
from app.state import state_hash

DAY = C.TICKS_PER_DAY
WEEK = DAY * C.DAYS_PER_WEEK


def run(coro):
    return asyncio.run(coro)


def events(sim, world="adaptive"):
    return sim.store.read(world)


def types(sim, world="adaptive", actor=None):
    return {e["type"] for e in events(sim, world) if actor is None or e["actor"] == actor}


def test_same_seed_gives_the_same_company():
    async def go():
        a, b = Simulation(seed=3), Simulation(seed=3)
        await a.advance(WEEK)
        await b.advance(WEEK)
        return state_hash(a.adaptive.state), state_hash(b.adaptive.state)
    first, second = run(go())
    assert first == second


def test_every_closed_day_replays_from_the_log():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(2 * WEEK)
        return [sim.replay(day) for day in range(1, 11)]
    for result in run(go()):
        assert result["verified"], result["day"]
        assert result["events"]


def test_four_roles_each_act_on_the_shared_records():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(WEEK)
        return sim
    sim = run(go())
    assert "deal.won" in types(sim, actor="sales")
    assert "work.completed" in types(sim, actor="ops")
    assert "invoice.issued" in types(sim, actor="finance")
    assert "ticket.triaged" in types(sim, actor="support")
    # a deal becomes a job becomes an invoice, across three different agents
    work = next(e for e in events(sim) if e["type"] == "work.completed" and e["data"]["bill"])
    assert any(e["type"] == "invoice.issued" and e["data"]["work"] == work["data"]["id"] for e in events(sim))


def test_company_self_corrects_without_a_human():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(2 * WEEK)
        return sim
    sim = run(go())
    log = events(sim)
    seen = [e["type"] for e in log]
    for step in ("ops.capacity_alert", "sales.mode_changed", "ops.budget_requested",
                 "finance.budget_approved", "ops.contractor_engaged"):
        assert step in seen, step
    assert not any(e["actor"] == "human" for e in log)
    assert any(e["type"] == "ops.capacity_alert" and e["data"]["level"] == "green" for e in log)
    # the static baseline never raises the alarm
    assert "ops.capacity_alert" not in types(sim, "baseline")


def test_adaptive_company_outperforms_the_baseline():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(4 * WEEK)
        return sim.summary()["kpi"]
    kpi = run(go())
    assert kpi["adaptive"]["churned"] < kpi["baseline"]["churned"]
    assert kpi["adaptive"]["profit"] > kpi["baseline"]["profit"]


def test_finance_failure_is_detected_covered_and_recovered():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(2 * DAY)
        sim.kill("finance")
        await sim.advance(DAY)
        during = dict(sim.adaptive.state["agents"]["finance"]), dict(sim.adaptive.state["company"])
        waiting = len(sim.adaptive.agents["finance"].mailbox)
        down = next(e for e in sim.adaptive.state["inbox"].values() if e["kind"] == "agent_down")
        invoices_before = len(sim.adaptive.state["invoices"])
        sim.resolve(down["id"], "restart")
        await sim.advance(2)
        return sim, during, waiting, invoices_before
    sim, (agent, company), waiting, invoices_before = run(go())
    assert agent["down"] and agent["killed"]
    assert company["terms"] == "deposit"  # Sales stopped offering credit
    assert waiting > 0  # deliveries queued for Finance instead of being lost
    seen = types(sim)
    assert {"watchdog.agent_down", "agent.compensating", "watchdog.agent_recovered"} <= seen
    assert not sim.adaptive.state["agents"]["finance"]["down"]
    assert len(sim.adaptive.state["invoices"]) > invoices_before  # backlog invoiced on return
    assert not sim.adaptive.agents["finance"].mailbox
    # the baseline has no watchdog: nobody noticed
    assert "watchdog.agent_down" not in types(sim, "baseline")


def test_each_role_failing_triggers_cover_from_another_role():
    async def go(name):
        sim = Simulation(seed=7)
        await sim.advance(2 * DAY)
        sim.kill(name)
        await sim.advance(DAY)
        return [e for e in events(sim) if e["type"] == "agent.compensating" and e["data"]["covering"] == name]
    for name in C.AGENTS:
        cover = run(go(name))
        assert cover, f"nobody covered for {name}"
        assert all(e["actor"] != name for e in cover)


def test_human_decision_changes_the_outcome():
    async def go():
        sim = Simulation(seed=7)
        esc = None
        for _ in range(4 * WEEK):
            await sim.advance(1)
            esc = next((e for e in sim.adaptive.state["inbox"].values()
                        if e["status"] == "open" and e["kind"] == "discount"), None)
            if esc:
                break
        assert esc, "no discount escalation in four weeks"
        assert sim.resolve(esc["id"], "approve")
        assert not sim.resolve(esc["id"], "decline")  # cannot be decided twice
        await sim.advance(1)
        return sim, esc
    sim, esc = run(go())
    lead = sim.adaptive.state["leads"][esc["ref"]]
    assert lead["discount"] == lead["sens"]
    assert lead["status"] in ("won", "lost")


def test_unanswered_escalation_takes_the_safe_default():
    async def go():
        sim = Simulation(seed=7)
        await sim.advance(4 * WEEK)
        return [e for e in events(sim) if e["type"] == "escalation.resolved"]
    resolved = run(go())
    assert resolved and all(e["data"]["by"] == "timeout" for e in resolved)
