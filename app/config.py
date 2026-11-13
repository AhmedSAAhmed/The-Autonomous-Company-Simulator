"""Every tunable number of the simulated company lives here."""

COMPANY_NAME = "Rimal Business Services"
CURRENCY = "AED"

# Clock: one tick is one working hour.
TICKS_PER_DAY = 8
START_HOUR = 9
DAYS_PER_WEEK = 5
MAX_DAY = 40

AGENTS = ["sales", "ops", "finance", "support"]

# Money
START_CASH = 60_000
DAILY_FIXED_COST = 2_300
CONTRACTOR_RATE = 120  # per hour
ONBOARDING_FEE_PCT = 50  # of first month's retainer

# Delivery
BASE_CAPACITY = 2  # staff hours delivered per tick
ONBOARDING_HOURS = 8
FIX_HOURS = 2
CONTRACTOR_EXTRA = 2  # extra hours per tick, per contractor
MAX_CONTRACTOR_EXTRA = 10
CONTRACTOR_DAYS = 3
BACKLOG_RED_DAYS = 2.5
BACKLOG_GREEN_DAYS = 1.8
BACKLOG_PAUSE_DAYS = 4  # beyond this Sales waitlists every new lead

# Sales
SALES_ACTIONS_PER_TICK = 3
MIN_SCORE = 35
THROTTLED_MIN_SCORE = 60
MAX_AUTO_DISCOUNT = 15
DISCOUNT_ESCALATION_VALUE = 6_000  # monthly retainer above which a deep discount needs a human
BASELINE_DISCOUNT = 10
LEAD_TTL_DAYS = 2

# Finance
INVOICE_TERMS_DAYS = 2
BUDGET_AUTHORITY_PCT = 15  # Finance approves spend up to this share of cash on hand
BUDGET_MIN_RUNWAY = 12
RUNWAY_RED_DAYS = 10
RUNWAY_GREEN_DAYS = 14
PLAN_AUTHORITY = 3_000  # overdue invoices above this go to a human

# Support
TICKETS_PER_TICK = 3
CREDIT_AUTHORITY = 500
OUTREACH_HEALTH = 50
CREDIT_HEALTH = 35

# Customers
LATE_PENALTY = 7
LATE_PENALTY_NOTIFIED = 3
TICKET_AGE_PENALTY = 4
CHURN_HEALTH = 30
CHURN_PROB = 0.4

# Control plane
HEARTBEAT_MISSES = 2
ESCALATION_TTL_TICKS = 8

# Market shocks: day -> lead multiplier
DEMAND_SPIKES = {3: 2.5, 4: 2.5, 5: 2.0, 12: 2.5, 13: 2.5, 23: 2.5, 24: 2.0}
