"""
One-time fix: creates missing Strategy + StrategyLeg rows for strategies
that are referenced by alert rules but don't exist in the DB.

The frontend stores strategy data in localStorage (Zustand persist).
Alert rules were created referencing frontend strategy UUIDs.
If the DB was reset or strategies were never properly saved, alert rules
point to non-existent strategy IDs and the alert engine has nothing to evaluate.

This script:
  1. Identifies strategy IDs in alert rules that have no matching DB strategy
  2. Tries to find leg info from other strategies' description JSONs
  3. Creates Strategy + StrategyLeg rows so the alert engine can evaluate conditions

Run once after identifying missing strategies, then restart the backend.

Usage:
  cd backend
  python fix_strategy_legs.py
"""
import asyncio
import json
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}[OK]{RESET}  {msg}")
def fail(msg): print(f"  {RED}[!!]{RESET}  {msg}")
def info(msg): print(f"  {CYAN}-->  {RESET}{msg}")
def head(msg): print(f"\n{BOLD}{YELLOW}{msg}{RESET}")


# ── Manual data for strategies that can't be auto-resolved ────────────────────
#
# If the auto-detection below fails, add entries here.
# Format:
#   strategy_id → {
#     "name":       display name,
#     "underlying": underlying symbol (e.g. "CRUDEOIL"),
#     "legs": [
#       {
#         "leg_id":      frontend leg UUID (from condition tree),
#         "symbol":      tradeable symbol,
#         "side":        "BUY" or "SELL",
#         "quantity":    int,
#         "entry_price": float (0.0 is fine — only LTP matters for alerts),
#       }
#     ]
#   }
#
MANUAL_STRATEGY_DATA: dict[str, dict] = {
    # ── CRUDEOIL strategy (confirmed from condition trees) ─────────────────
    "97419197-692f-45aa-8d0e-7318f9a20d59": {
        "name":       "CRUDEOIL Strategy",
        "underlying": "CRUDEOIL",
        "legs": [
            {
                "leg_id":      "59d98f94-86c5-47ce-8660-1dde1d909090",
                "symbol":      "CRUDEOIL18MAY26FUT",
                "side":        "BUY",
                "quantity":    1,
                "entry_price": 0.0,   # update if you want accurate MTM
            }
        ],
    },
    # Add more strategies here if needed:
    # "aa648ede-a4cd-45d5-8bd3-953c905a2088": {
    #     "name": "My Options Strategy",
    #     "underlying": "NIFTY",
    #     "legs": [
    #         {
    #             "leg_id": "e8a5c321-acfb-4b9d-8893-987e4be11d00",
    #             "symbol": "NIFTY24MAY26CE24000",
    #             "side": "SELL",
    #             "quantity": 1,
    #             "entry_price": 0.0,
    #         }
    #     ],
    # },
}


async def main():
    from core.database import init_db, AsyncSessionLocal
    from models.relational import Strategy, StrategyLeg
    from alerts.models import AlertRuleBuilder
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    await init_db()

    head("Step 1 — Find strategy IDs in alert rules")
    async with AsyncSessionLocal() as db:
        rule_res = await db.execute(
            select(AlertRuleBuilder.strategy_id, AlertRuleBuilder.name)
            .where(AlertRuleBuilder.is_active == True)  # noqa
        )
        rules = rule_res.fetchall()

    alert_sids = {}  # strategy_id → list of rule names
    for r in rules:
        sid = str(r[0])
        alert_sids.setdefault(sid, []).append(r[1])

    ok(f"Found {len(alert_sids)} strategy IDs in active alert rules:")
    for sid, rnames in alert_sids.items():
        print(f"    {sid}  → rules: {rnames}")

    head("Step 2 — Check which strategies exist in DB")
    async with AsyncSessionLocal() as db:
        strat_res = await db.execute(
            select(Strategy).options(selectinload(Strategy.legs))
        )
        all_strategies = {str(s.id): s for s in strat_res.scalars().all()}

    ok(f"DB has {len(all_strategies)} strategy/strategies:")
    for sid in all_strategies:
        s = all_strategies[sid]
        print(f"    {sid}  legs={len(s.legs)}  name={s.name}")

    missing_sids = set(alert_sids.keys()) - set(all_strategies.keys())
    if not missing_sids:
        ok("All alert-rule strategies exist in DB — no rows to create")
        info("If alerts still don't fire, check StrategyLeg UUIDs vs condition tree leg_ids")

        # Still check for leg UUID mismatches
        head("Step 2b — Check leg UUID mismatches")
        from alerts.models import AlertRuleBuilder
        async with AsyncSessionLocal() as db:
            full_rules = await db.execute(
                select(AlertRuleBuilder).where(AlertRuleBuilder.is_active == True)  # noqa
            )
            all_rules = full_rules.scalars().all()
        for rule in all_rules:
            tree = rule.condition_tree or {}
            for cond in tree.get("conditions", []):
                if cond.get("scope") == "LEG" and cond.get("leg_id"):
                    fid = cond["leg_id"]
                    sid = str(rule.strategy_id)
                    s = all_strategies.get(sid)
                    if s:
                        db_ids = {str(l.id) for l in s.legs}
                        if fid not in db_ids:
                            fail(f"  Leg UUID mismatch in rule '{rule.name}': "
                                 f"condition has {fid[:16]}... but DB has {db_ids}")
                        else:
                            ok(f"  Leg UUID OK in rule '{rule.name}': {fid[:16]}...")
        return

    fail(f"{len(missing_sids)} strategy/strategies referenced by alert rules are NOT in DB:")
    for sid in missing_sids:
        print(f"    {sid}  → rules: {alert_sids[sid]}")

    head("Step 3 — Create missing strategies + legs")
    created = 0

    async with AsyncSessionLocal() as db:
        for sid in missing_sids:
            manual = MANUAL_STRATEGY_DATA.get(sid)
            if not manual:
                fail(f"No manual data for {sid} — skipping")
                info("  Add an entry to MANUAL_STRATEGY_DATA in this script, then re-run")
                continue

            print(f"\n  Creating strategy: {BOLD}{sid}{RESET}")
            print(f"  name={manual['name']}  underlying={manual['underlying']}")

            strategy = Strategy(
                id=uuid.UUID(sid),
                name=manual["name"],
                underlying=manual.get("underlying"),
                status="active",
            )
            db.add(strategy)

            for leg_def in manual.get("legs", []):
                leg_id_str = leg_def.get("leg_id")
                try:
                    leg_id = uuid.UUID(leg_id_str) if leg_id_str else uuid.uuid4()
                except (ValueError, AttributeError):
                    leg_id = uuid.uuid4()

                side   = leg_def.get("side", "BUY")
                symbol = leg_def["symbol"]
                qty    = int(leg_def.get("quantity", 1))
                entry  = float(leg_def.get("entry_price", 0.0))

                new_leg = StrategyLeg(
                    id=leg_id,
                    strategy_id=uuid.UUID(sid),
                    symbol=symbol,
                    action=side,
                    quantity=qty,
                    entry_price=entry,
                    status="filled",
                )
                db.add(new_leg)
                ok(f"  Leg: id={str(leg_id)[:16]}...  symbol={symbol}  side={side}  qty={qty}")

            created += 1

        await db.commit()

    head("Result")
    if created:
        ok(f"Created {created} strategy/strategies with legs")
        print()
        print(f"  {YELLOW}Next steps:{RESET}")
        print(f"  1. Restart the backend:  uvicorn main:app --reload")
        print(f"  2. Wait for CRUDEOIL18MAY26FUT ticks to arrive")
        print(f"  3. When price crosses the configured threshold, alert will fire")
        print()
        print(f"  {YELLOW}Note:{RESET} entry_price is set to 0.0 for auto-created legs.")
        print(f"  MTM and PNL% metrics will be inaccurate until you update entry prices.")
        print(f"  LTP-based alerts (CROSS_BELOW / CROSS_ABOVE) are not affected.")
    else:
        fail("No strategies created — add entries to MANUAL_STRATEGY_DATA above")


if __name__ == "__main__":
    asyncio.run(main())
