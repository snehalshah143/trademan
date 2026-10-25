"""
Alert Simulation — tests alert conditions against simulated CRUDEOIL price ticks.

Reads real condition trees from the DB. Constructs EvaluationContext with
simulated prices and runs the evaluator directly — no server needed.

Usage:
  cd backend
  python simulate_alerts.py

Tests:
  - CROSS_BELOW 8935 : fires when price moves from above 8935 to below/at 8935
  - CROSS_ABOVE 8965 : fires when price moves from below 8965 to above/at 8965
"""
import asyncio
import json
import sys
import os
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

# Force UTF-8 on Windows (avoids cp1252 UnicodeEncodeError)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}[OK]  {msg}{RESET}")
def fail(msg): print(f"  {RED}[!!]  {msg}{RESET}")
def info(msg): print(f"  {CYAN}-->   {msg}{RESET}")
def head(msg): print(f"\n{BOLD}{YELLOW}{msg}{RESET}")


# ── Context builder helper ─────────────────────────────────────────────────────

def make_ctx(leg_id: str, ltp: float,
             entry_price: float = 8900.0,
             quantity: int = 1,
             action: str = "BUY") -> dict:
    """Build a minimal EvaluationContext for one leg."""
    mult = 1 if action == "BUY" else -1
    mtm  = mult * (ltp - entry_price) * quantity
    return {
        "strategy_id":      "97419197-692f-45aa-8d0e-7318f9a20d59",
        "mtm":              mtm,
        "pnl_pct":          (mtm / (entry_price * quantity) * 100) if entry_price else 0.0,
        "spot":             ltp,
        "leg_ltps":         {leg_id: ltp},
        "leg_entry_prices": {leg_id: entry_price},
        "leg_quantities":   {leg_id: quantity},
        "leg_sides":        {leg_id: action},
        "indicators":       {},
        "candles":          {},
    }


# ── Scenario runner ───────────────────────────────────────────────────────────

def run_scenario(label: str, tree: dict, prev_ltp: float, curr_ltp: float,
                 leg_id: str, entry: float = 8900.0) -> bool:
    from alerts.rule_evaluator import evaluate_group
    from alerts.alert_cache import EvaluationContext

    prev_ctx = EvaluationContext(make_ctx(leg_id, prev_ltp, entry))
    curr_ctx = EvaluationContext(make_ctx(leg_id, curr_ltp, entry))

    fired = evaluate_group(tree, curr_ctx, prev_ctx)
    status = f"{GREEN}FIRED{RESET}" if fired else f"{RED}did not fire{RESET}"
    print(f"  prev={prev_ltp:>8.2f} → curr={curr_ltp:>8.2f}  │  {label:35s} │  {status}")
    return fired


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    # Initialise DB connection (needed to load rules)
    from core.database import init_db, AsyncSessionLocal
    from alerts.models import AlertRuleBuilder
    from sqlalchemy import select

    await init_db()

    # ── 1. Load alert rules from DB ───────────────────────────────────────────
    head("Step 1 — Loading alert rules from DB")
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(AlertRuleBuilder)
            .where(AlertRuleBuilder.strategy_id == "97419197-692f-45aa-8d0e-7318f9a20d59")
        )
        rules = res.scalars().all()

    if not rules:
        print(f"  {RED}No alert rules found for the CRUDEOIL strategy.{RESET}")
        print("  strategy_id expected: 97419197-692f-45aa-8d0e-7318f9a20d59")
        return

    ok(f"Found {len(rules)} alert rule(s):")
    for r in rules:
        conds = r.condition_tree.get("conditions", [])
        for c in conds:
            info(f"  [{r.name}]  {c['scope']} {c['metric']} {c['operator']} {c.get('value')}  "
                 f"leg_id={c.get('leg_id','—')[:8]}...")

    LEG_ID = "59d98f94-86c5-47ce-8660-1dde1d909090"

    # ── 2. Check evaluator imports ────────────────────────────────────────────
    head("Step 2 — Verifying evaluator")
    try:
        from alerts.rule_evaluator import evaluate_group
        from alerts.alert_cache import EvaluationContext
        ok("rule_evaluator and alert_cache imported successfully")
    except Exception as e:
        fail(f"Import failed: {e}")
        return

    # ── 3. Simulate CROSS_BELOW 8935 ─────────────────────────────────────────
    head("Step 3 — Simulating: Price CROSS_BELOW 8935")
    print(f"  {'Scenario':<40}{'Condition':<37}  Result")
    print("  " + "─" * 90)

    cross_below_rule = next((r for r in rules if "below" in r.name.lower() or
                             any(c.get("operator") == "CROSS_BELOW"
                                 for c in r.condition_tree.get("conditions", []))), None)

    if cross_below_rule:
        tree = cross_below_rule.condition_tree
        run_scenario("prev=8940 → curr=8930  (crosses below 8935)", tree, 8940, 8930, LEG_ID)
        run_scenario("prev=8936 → curr=8935  (touches threshold)",  tree, 8936, 8935, LEG_ID)
        run_scenario("prev=8935 → curr=8934  (prev = threshold)",   tree, 8935, 8934, LEG_ID)
        run_scenario("prev=8920 → curr=8910  (already below, no cross)", tree, 8920, 8910, LEG_ID)
        run_scenario("prev=8934 → curr=8936  (moving UP, wrong dir)", tree, 8934, 8936, LEG_ID)
        run_scenario("prev=8940 → curr=8935  (exactly at threshold)", tree, 8940, 8935, LEG_ID)
    else:
        fail("Could not find a CROSS_BELOW rule")

    # ── 4. Simulate CROSS_ABOVE 8965 ─────────────────────────────────────────
    head("Step 4 — Simulating: Price CROSS_ABOVE 8965")
    print(f"  {'Scenario':<40}{'Condition':<37}  Result")
    print("  " + "─" * 90)

    cross_above_rule = next((r for r in rules if "above" in r.name.lower() or
                              any(c.get("operator") == "CROSS_ABOVE"
                                  for c in r.condition_tree.get("conditions", []))), None)

    if cross_above_rule:
        tree = cross_above_rule.condition_tree
        run_scenario("prev=8960 → curr=8970  (crosses above 8965)", tree, 8960, 8970, LEG_ID)
        run_scenario("prev=8964 → curr=8965  (touches threshold)",  tree, 8964, 8965, LEG_ID)
        run_scenario("prev=8965 → curr=8966  (prev = threshold)",   tree, 8965, 8966, LEG_ID)
        run_scenario("prev=8970 → curr=8980  (already above, no cross)", tree, 8970, 8980, LEG_ID)
        run_scenario("prev=8966 → curr=8963  (moving DOWN, wrong dir)", tree, 8966, 8963, LEG_ID)
    else:
        fail("Could not find a CROSS_ABOVE rule")

    # ── 5. Full pipeline simulation (ticks in sequence) ────────────────────────
    head("Step 5 — Sequential tick simulation (realistic price feed)")
    print("  Simulates a sequence of prices the way the pipeline would process them.")
    print("  The evaluator maintains prev/curr across ticks.\n")

    # Price sequence: starts above both thresholds, falls through 8935, bounces above 8965
    tick_sequence = [
        8980, 8972, 8968, 8966, 8963, 8958,
        8950, 8940, 8935, 8930, 8925,   # crosses BELOW 8935 between 8940 and 8935
        8928, 8935, 8940, 8950, 8960,
        8965, 8970, 8975,                # crosses ABOVE 8965 between 8960 and 8965
        8968, 8965, 8960,
    ]

    prev_ctx = None

    for i, price in enumerate(tick_sequence):
        curr_ctx_raw = make_ctx(LEG_ID, price, entry_price=8900.0)
        from alerts.alert_cache import EvaluationContext
        curr_ctx = EvaluationContext(curr_ctx_raw)

        if prev_ctx is None:
            prev_ctx = curr_ctx
            print(f"  tick[{i:02d}]  price={price:.2f}  (seed tick — establishing baseline)")
            continue

        fired_alerts = []
        for r in rules:
            from alerts.rule_evaluator import evaluate_group
            try:
                if evaluate_group(r.condition_tree, curr_ctx, prev_ctx):
                    fired_alerts.append(r.name)
            except Exception:
                pass

        prev_price = list(prev_ctx.get("leg_ltps", {}).values())[0]
        flag = f"  {GREEN}◄ ALERT: {', '.join(fired_alerts)}{RESET}" if fired_alerts else ""
        print(f"  tick[{i:02d}]  price={price:>8.2f}  (prev={prev_price:>8.2f}){flag}")

        prev_ctx = curr_ctx

    # ── 6. Summary of what makes alerts fire ──────────────────────────────────
    head("Step 6 — Summary: When will these alerts fire in production?")

    print(f"""
  Alert: "Price crosses below price"  (CROSS_BELOW 8935)
  ─────────────────────────────────────────────────────
  ✓ Fires when: previous tick > 8935  AND  current tick ≤ 8935
  ✗ Will NOT fire if: price is already below 8935 when backend starts
  ✗ Will NOT fire on first tick (no prev context yet)
  → Recommendation: Use LT 8935 with cooldown if you want continuous alerts

  Alert: "price above level"  (CROSS_ABOVE 8965)
  ─────────────────────────────────────────────────────
  ✓ Fires when: previous tick < 8965  AND  current tick ≥ 8965
  ✗ Will NOT fire if: price is already above 8965 when backend starts

  Both alerts:
    strategy_id  = 97419197-692f-45aa-8d0e-7318f9a20d59  (frontend UUID)
    leg_id       = 59d98f94-86c5-47ce-8660-1dde1d909090
    symbol       = CRUDEOIL18MAY26FUT  (inferred from strategy description JSON)
    cooldown     = 60s
""")

    ok("Simulation complete.")


if __name__ == "__main__":
    asyncio.run(main())
