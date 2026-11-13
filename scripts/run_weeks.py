"""Run the company headless and print adaptive vs baseline. Usage: python scripts/run_weeks.py [weeks] [seed]"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config as C  # noqa: E402
from app.engine import Simulation  # noqa: E402


async def main(weeks, seed):
    sim = Simulation(seed=seed)
    keys = ("cash", "revenue", "profit", "mrr", "customers", "churned", "backlog_hours", "on_time_pct")
    print(f"{'week':>4} {'world':>9} " + " ".join(f"{k:>13}" for k in keys))
    for week in range(1, weeks + 1):
        await sim.advance(C.DAYS_PER_WEEK * C.TICKS_PER_DAY)
        for world in sim.worlds:
            k = sim.summary()["kpi"][world.name]
            print(f"{week:>4} {world.name:>9} " + " ".join(f"{k[x]:>13}" for x in keys))
    print("replay day 3 verified:", sim.replay(3)["verified"])


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 4, int(sys.argv[2]) if len(sys.argv) > 2 else 7))
