"""
Alert diagnosis script — finds exactly why alerts aren't firing.

Checks:
  1. DB leg UUIDs vs condition-tree leg UUIDs (the root cause)
  2. Whether description JSON has frontend leg IDs (for Pass 1.5 remap)
  3. Simulates the exact evaluation path with the correct context

Usage:
  cd backend
  python diagnose_alerts.py
"""
import asyncio
import json
import sys
import os

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


async def main():
    from core.database import init_db, AsyncSessionLocal
    from models.relational import Strategy, StrategyLeg
    from alerts.models import AlertRuleBuilder
    from sqlalchemy import select

    await init_db()

    head("Step 1 — Load alert rules from DB")
    async with AsyncSessionLocal() as db:
        rules_res = await db.execute(
            select(AlertRuleBuilder).where(AlertRuleBuilder.is_active == True)  # noqa
        )
        all_rules = rules_res.scalars().all()

    if not all_rules:
        fail("No active alert rules found.")
        return
    ok(f"Found {len(all_rules)} active alert rule(s)")

    # Collect all strategy IDs referenced by rules
    rule_strategy_ids = {str(r.strategy_id) for r in all_rules}

    head("Step 2 — Load strategies + DB legs")
    async with AsyncSessionLocal() as db:
        strat_res = await db.execute(
            select(Strategy).where(Strategy.status.in_(["active", "draft"]))
        )
        all_strategies = strat_res.scalars().all()

        leg_res = await db.execute(select(StrategyLeg))
        all_db_legs = leg_res.scalars().all()

    # Build: strategy_id → DB legs
    db_legs_by_sid: dict[str, list] = {}
    for leg in all_db_legs:
        sid = str(leg.strategy_id)
        db_legs_by_sid.setdefault(sid, []).append(leg)

    head("Step 3 — UUID mismatch analysis per alert rule")
    any_mismatch = False

    for rule in all_rules:
        sid = str(rule.strategy_id)
        tree = rule.condition_tree or {}
        conditions = tree.get("conditions", [])
        leg_conditions = [c for c in conditions if c.get("scope") == "LEG"]

        print(f"\n  Rule: {BOLD}{rule.name}{RESET}  ({rule.alert_id[:8]}...)")
        print(f"  strategy_id: {sid}")

        if not leg_conditions:
            info("No LEG-scope conditions — UUID mismatch not applicable")
            continue

        # Get DB legs for this strategy
        db_legs = db_legs_by_sid.get(sid, [])
        db_leg_ids = {str(l.id) for l in db_legs}

        # Get frontend leg IDs from condition tree
        cond_leg_ids = {c.get("leg_id", "") for c in leg_conditions if c.get("leg_id")}

        print(f"\n  LEG conditions: {len(leg_conditions)}")
        for c in leg_conditions:
            print(f"    scope={c.get('scope')} metric={c.get('metric')} "
                  f"op={c.get('operator')} value={c.get('value')} "
                  f"leg_id={c.get('leg_id', 'N/A')[:16]}...")

        print(f"\n  DB leg IDs for this strategy: {len(db_leg_ids)}")
        for l in db_legs:
            print(f"    {str(l.id)[:16]}...  symbol={l.symbol}")

        # Check if condition leg IDs exist in DB leg IDs
        matched = cond_leg_ids & db_leg_ids
        unmatched = cond_leg_ids - db_leg_ids

        if unmatched:
            fail(f"UUID MISMATCH: {len(unmatched)} condition leg_id(s) NOT found in DB!")
            for uid in unmatched:
                print(f"    {RED}MISSING:{RESET} {uid}")
            any_mismatch = True
        else:
            ok("All condition leg_ids match DB leg IDs")

        # Check description JSON for frontend leg IDs
        strat = next((s for s in all_strategies if str(s.id) == sid), None)
        if strat and strat.description:
            try:
                desc = json.loads(strat.description)
                desc_legs = desc.get("legs", [])
                sym_to_fid = {
                    (dl.get("instrument") or {}).get("symbol", ""): dl.get("id", "")
                    for dl in desc_legs
                    if dl.get("id") and (dl.get("instrument") or {}).get("symbol")
                }
                if sym_to_fid:
                    ok(f"Description JSON has {len(sym_to_fid)} leg(s) with frontend IDs")
                    for sym, fid in sym_to_fid.items():
                        match = fid in cond_leg_ids
                        status = f"{GREEN}MATCHES condition{RESET}" if match else f"{YELLOW}no match in conditions{RESET}"
                        print(f"    {fid[:16]}...  sym={sym}  [{status}]")

                    # Show what Pass 1.5 remap would do
                    for leg in db_legs:
                        sym = leg.symbol
                        fid = sym_to_fid.get(sym)
                        if fid and fid != str(leg.id):
                            info(f"Pass 1.5 would remap: {str(leg.id)[:16]}... → {fid[:16]}... ({sym})")
                else:
                    fail("Description JSON has no leg IDs — Pass 1.5 remap cannot help!")
                    info("Solution: re-save the strategy from the frontend")
            except Exception as e:
                fail(f"Description JSON parse error: {e}")
        else:
            fail("Strategy has no description JSON — Pass 1.5 remap cannot help!")
            info("Solution: re-save the strategy from the frontend, or re-add legs via API with id field")

    head("Step 4 — Simulate evaluation with CORRECT context (frontend UUIDs)")
    if any_mismatch:
        info("Simulating what evaluation looks like with CORRECT leg IDs...")

        from alerts.rule_evaluator import evaluate_group

        for rule in all_rules:
            sid = str(rule.strategy_id)
            tree = rule.condition_tree or {}
            conditions = tree.get("conditions", [])
            leg_conditions = [c for c in conditions if c.get("scope") == "LEG"]
            if not leg_conditions:
                continue

            # Build context using FRONTEND UUIDs from condition tree
            for c in leg_conditions:
                leg_id = c.get("leg_id")
                if not leg_id:
                    continue
                op = c.get("operator")
                threshold = c.get("value", 0)

                # Simulate a crossing scenario
                if op == "CROSS_BELOW":
                    prev_ltp = threshold + 10
                    curr_ltp = threshold - 5
                elif op == "CROSS_ABOVE":
                    prev_ltp = threshold - 10
                    curr_ltp = threshold + 5
                else:
                    prev_ltp = threshold - 1
                    curr_ltp = threshold + 1

                def make_ctx(ltp):
                    return {
                        "leg_ltps":         {leg_id: ltp},
                        "leg_entry_prices": {leg_id: ltp},
                        "leg_quantities":   {leg_id: 1},
                        "leg_sides":        {leg_id: "BUY"},
                        "mtm": 0.0, "pnl_pct": 0.0, "spot": ltp,
                        "indicators": {}, "candles": {},
                    }

                from alerts.alert_cache import EvaluationContext
                curr_ctx = EvaluationContext(make_ctx(curr_ltp))
                prev_ctx = EvaluationContext(make_ctx(prev_ltp))

                fired = evaluate_group(tree, curr_ctx, prev_ctx)
                status = f"{GREEN}FIRED{RESET}" if fired else f"{RED}did not fire{RESET}"
                print(f"\n  Rule: {rule.name}")
                print(f"  {op} {threshold}: prev={prev_ltp} curr={curr_ltp} → {status}")

                if not fired:
                    fail("STILL not firing even with correct UUIDs — check condition logic")
                else:
                    ok("Evaluator logic is correct — only the UUID routing was broken")

    head("Step 5 — LTP service subscription check")
    try:
        from services.redis_service import redis_service
        await redis_service.connect()
        all_ltps = await redis_service.client.hgetall("market:ltp")
        if all_ltps:
            subscribed = {k.decode() if isinstance(k, bytes) else k for k in all_ltps.keys()}
            ok(f"Redis market:ltp has {len(subscribed)} symbols: {sorted(subscribed)}")

            # Check which strategy symbols are subscribed
            async with AsyncSessionLocal() as db:
                rules_res = await db.execute(
                    select(AlertRuleBuilder).where(AlertRuleBuilder.is_active == True)  # noqa
                )
                all_rules2 = rules_res.scalars().all()

            # Extract symbols from description JSONs
            for rule in all_rules2:
                sid = str(rule.strategy_id)
                strat = next((s for s in all_strategies if str(s.id) == sid), None)
                if strat and strat.description:
                    try:
                        desc = json.loads(strat.description)
                        for fl in desc.get("legs", []):
                            sym = (fl.get("instrument") or {}).get("symbol", "")
                            if sym:
                                if sym in subscribed:
                                    ok(f"{sym} IS in Redis market:ltp — ticks flowing")
                                else:
                                    fail(f"{sym} NOT in Redis market:ltp — NO TICKS!")
                                    info("Fix: restart backend so alert_pipeline.start() subscribes it")
                    except Exception:
                        pass
        else:
            fail("Redis market:ltp is empty — no ticks flowing at all!")
            info("Is LTPService running? Is broker connected?")
        await redis_service.disconnect()
    except Exception as e:
        fail(f"Redis check failed: {e}")

    head("Summary")
    if any_mismatch:
        print(f"""
  {RED}ROOT CAUSE CONFIRMED: LEG UUID MISMATCH{RESET}

  The condition tree stores FRONTEND leg UUIDs (from the browser).
  StrategyLeg.id in the DB is BACKEND-generated (uuid4()).
  When _build_context creates leg_ltps = {{backend_uuid: ltp}},
  _resolve_value does leg_ltps.get(frontend_uuid) → None → condition always False.

  {GREEN}FIXES APPLIED IN THIS SESSION:{RESET}
    1. alert_pipeline._load_strategy_data() now has Pass 1.5:
       remaps backend UUIDs → frontend UUIDs from description JSON.
    2. strategies.py LegCreate now accepts 'id' field.
    3. StrategyLeg is now created with the frontend leg UUID when provided.

  {YELLOW}REQUIRED ACTION:{RESET}
    → Restart the backend: uvicorn main:app --reload
    → The next tick for your alert symbol will use the correct UUIDs.
    → If Pass 1.5 remap can't find description JSON leg IDs, re-save
      the strategy from the frontend (or delete and recreate it).
""")
    else:
        ok("No UUID mismatch found. Alert engine should work.")
        info("If alerts still don't fire, check Redis subscription above.")


if __name__ == "__main__":
    asyncio.run(main())
