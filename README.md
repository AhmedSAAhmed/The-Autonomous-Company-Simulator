# The Autonomous Company Simulator

A small company that runs as a loop of accountable AI agents on one shared ledger, not as a stack of disconnected tools.

**Rimal Business Services** is a fictional Gulf B2B services firm (payroll, bookkeeping, licensing, HR, visa processing on monthly retainers). Four agents run it hour by hour against a live market stream. They read and write the same records, signal each other, cover for each other when one fails, and send a human only the decisions they are not allowed to make.

- **Live demo:** _add your Render URL here_
- **Thesis:** [docs/thesis.md](docs/thesis.md)

> The demo is hosted on Render's free tier, which sleeps after 15 minutes without traffic. The first request after a sleep takes about a minute. The company is shared: everyone who opens the link watches and steers the same run.

## What to look at

| In the demo | What it shows |
|---|---|
| Press **Play** | A working week unfolds one hour every couple of seconds. Leads arrive, deals close, jobs are delivered, invoices go out, cash moves. |
| **Human inbox** | A discount, spend, write-off or credit above an agent's authority waits for you. Ignore it and a safe default applies after eight working hours. |
| **Switch off** an agent | The failure test. The watchdog notices after two missed heartbeats, tells the others, and pages you. Watch the other agents change behaviour. |
| **Adaptive vs. baseline** | Two companies face the identical market from one seed. One listens to its own signals, one follows a fixed playbook. |
| **Replay** | Pick a closed day. It is rebuilt from the SQLite event log alone and its state hash is compared with the one recorded live. |

A good 90-second path: Play, answer one inbox item, wait for day 4 when a demand spike turns the backlog red and the company throttles sales and hires a contractor by itself, switch Finance off, restart it from the inbox, then replay day 4.

## Architecture

```
 market stream ──▶ event bus ──▶ shared ledger (append-only log in SQLite)
 leads, payments,     │                 ▲
 tickets, spikes      ▼                 │ every change is an event
        ┌────────┬────────┬─────────┬─────────┐
        │ Sales  │  Ops   │ Finance │ Support │   each: charter, mailbox, authority limits
        └────────┴────────┴─────────┴─────────┘
                      │ heartbeats
                      ▼
                  watchdog ──▶ human inbox ──▶ decisions flow back as events
```

**Shared state.** State is a plain dictionary of records (leads, customers, jobs, invoices, tickets, inbox). Nothing edits it directly. Every change is an event applied by a pure reducer in [app/state.py](app/state.py) and appended to a SQLite log. Folding the log from the first event rebuilds the company exactly, which is what makes replay real and not a recording.

**Agents.** Each role in [app/agents/](app/agents/) is a separate class with its own mailbox, its own charter in [company/roles/](company/roles/), and its own authority limits. An agent learns what a colleague did only through events on the bus. A deal becomes a job becomes an invoice across three different agents and no function call between them.

**Judgement.** Bookkeeping is deterministic. Judgement calls (scoring a lead, triaging a ticket, wording a payment reminder) go to an LLM with the role's charter as the system prompt, Groq first with Gemini as fallback. Each call is rate limited and has a rule-based answer underneath, so a rate limit degrades the demo instead of breaking it. Events decided by the model are tagged `LLM` in the stream. LLM and human decisions are stored in the log, so replay stays exact.

**Event-driven inputs.** The market in [app/market.py](app/market.py) is a stream, not a snapshot: leads land at the hour the market decides, customers pay or do not, tickets open, demand spikes hit. The browser receives every event over server-sent events.

### Escalation rules

| Agent | Goes to a human when | If nobody answers in 8 working hours |
|---|---|---|
| Sales | Discount above 15% on a retainer of AED 6,000 a month or more | Counter at 15% |
| Finance | Spend above 15% of cash, or runway under 12 days | Deny |
| Finance | Invoice above AED 3,000 three days overdue after two reminders | Offer a payment plan |
| Support | Goodwill credit above AED 500 | No credit |
| Ops | Contractor spend needed while Finance is down | Deny |
| Watchdog | Any agent misses two heartbeats | Restart the agent |

All thresholds live in [app/config.py](app/config.py).

### Self-correction, no human involved

When a demand spike pushes the delivery backlog past 2.5 working days:

1. Ops raises a capacity alert on the shared record and asks Finance for contractor budget sized to the backlog.
2. Finance checks runway and approves within its authority.
3. Sales reads the alert, waitlists weaker leads and sells the rest at a premium with later start dates.
4. Ops engages the contractor. When the backlog falls under 1.8 days, Ops clears the alert and Sales reopens intake.

### Failure test

Switching an agent off stops its heartbeats. Its mailbox keeps every event it misses.

| Role down | Who covers | How |
|---|---|---|
| Finance | Sales, Ops, Support | Deposit-only terms; contractor spend goes straight to the human; goodwill credits are held. On return Finance invoices everything in its mailbox. |
| Ops | Sales, Support | No start dates promised and weaker leads waitlisted; customers are warned about delays before they ask, which softens the damage to their health. |
| Sales | Support | Holding replies keep new leads from going cold. |
| Support | Ops | Tickets untriaged for half a day are picked up directly as urgent fixes. |

The baseline company has no watchdog and no cover, so the same failure simply costs it.

## Adaptive vs. baseline

Both companies run on the same seed. Seed 7, four weeks, rules only, no human answers:

| | Adaptive | Static baseline |
|---|---|---|
| Cash | AED 188,720 | AED 97,750 |
| Profit to date | AED 151,545 | AED 47,775 |
| Monthly recurring revenue | AED 227,700 | AED 40,500 |
| Customers lost | 0 | 32 |
| On-time delivery | 87% | 26% |

Reproduce it with `python scripts/run_weeks.py 4 7`. The baseline is deliberately rigid: it keeps selling at a flat discount into a full delivery queue, which is the failure mode the signals exist to prevent. It is a lower bound, not a model of a well-run human company.

## Run it

```bash
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. No API key is needed: without one the agents run on their rule-based judgement. To switch the LLM on, copy `.env.example` to `.env` and add a Groq key.

```bash
pytest
```

The tests check the claims above: same seed gives the same company, every closed day replays from the log, the company self-corrects with no human event, each role's failure is covered by another role, and a human decision changes the outcome.

### Deploy on Render

`render.yaml` describes a free web service. Create a Blueprint from this repository and set `GROQ_API_KEY`. Keep it at one worker: the event bus is in-process. The free tier's disk is ephemeral, so the company starts fresh after each sleep.

## Honest limits

- **Roles that still need humans.** Pricing exceptions, write-offs, spend beyond authority and restarting a failed role stay with a person by design. Hiring, contracts, regulators and relationships are not modelled at all.
- **The market is simulated.** Customers, payments and churn follow seeded probabilities, not real behaviour.
- **The LLM is advisory.** It scores, classifies and writes. Money movement and state changes are rule-bound and capped by each role's authority.
- **One process.** The bus and mailboxes live in memory. A production version would put them on a durable queue and restore mailboxes from the log on restart.
- **Fast-forward uses rules only.** "Next day" and "Run the week" skip LLM calls to stay inside the free tier. Live play uses them.

## Layout

```
app/
  state.py      shared records, reducers, fold, state hash
  world.py      one company: bus, mailboxes, watchdog, human inbox
  market.py     the seeded outside world
  agents/       sales, ops, finance, support
  engine.py     runs adaptive and baseline side by side, replay
  llm.py        provider chain with rate limit and fallback
  main.py       FastAPI: page, JSON API, event stream
  static/       the dashboard
company/roles/  one charter per agent, loaded as its system prompt
tests/          the claims, executable
```
