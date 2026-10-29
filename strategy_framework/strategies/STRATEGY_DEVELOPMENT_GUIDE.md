# Strategy Development Guide
# Trademan Strategy Framework — Standards, Patterns & Architectural Decisions

> **Living document.** Every new pattern, bug fix, or architectural decision applied to any strategy
> must be recorded here. All new strategies must be built following this guide. Refactoring of older
> strategies must align them to these standards.
>
> Reference strategies:
> - `strategies/directional/commodity/crudeoil_intraday_future_long_directional_3Min/`
> - `strategies/credit_spread/index_credit_spread_directional_weekly/`

---

## 1. Directory Structure

Every strategy lives under `strategies/<type>/<asset_class>/<strategy_name>/`.

```
strategies/
  <type>/                        # directional | credit_spread | iron_fly | etc.
    <asset_class>/               # commodity | equity | index
      <strategy_name>/
        __init__.py
        _engine.py               # indicator engine + entry/exit/reentry evaluators (local)
        execution_<name>.py      # waits for signal, places entry order, exits immediately
        monitoring_<name>.py     # recovers open position, manages SL/exit via WebSocket
        final_<name>.py          # orchestrator: loops execution → monitoring
        config/
          execution_settings.yaml
          monitoring_settings.yaml
        tests/
          test_<name>.py
        logs/                    # auto-created; gitignored
```

**Rule:** the strategy folder name must be descriptive and stable — it becomes the Python module path
used in `python -m strategies.<type>.<asset_class>.<name>.final_<name>`.

---

## 2. Script Roles

### `_engine.py`
Strategy-local. Contains:
- `IndicatorState` dataclass — snapshot of all computed indicator values
- `IndicatorEngine` class — seeds from history, updates O(1) per candle
- `evaluate_entry(ind, direction)` — pure function, returns bool
- `evaluate_exit(ind, ...)` — pure function, returns exit reason string or None
- `evaluate_reentry(ind, direction)` — pure function, returns bool (if strategy has re-entry)
- `RollingLowWindow` / `RollingHighWindow` — track M3 candle extremes
- Exit reason constants (`EXIT_RSI_CROSS_BELOW`, `EXIT_TRAILING_SL`, `EXIT_PROFIT_TARGET`, etc.)

**Rule:** `_engine.py` has zero side effects. No logging of decisions (only DEBUG indicator values),
no DB access, no broker calls. Evaluator functions are pure and unit-testable.

### `execution_<name>.py`
- Loads config, connects OpenAlgo, selects expiry
- Seeds candle builders + indicator engine from history warm-up
- Subscribes WebSocket → feeds ticks into `SignalWatcher`
- `SignalWatcher.on_tick()` feeds both builders; on M1 close calls `evaluate_entry()`
- On signal: builds leg, calls `EntryExecutor.execute()`, persists to DB, exits process (sys.exit 0)
- Idempotency guard: checks for existing open position before subscribing WebSocket

### `monitoring_<name>.py`
- Recovers open position from DB (primary) or positionbook API (fallback for live modes)
- Seeds candle builders + indicator engine from warm-up history
- Subscribes WebSocket → feeds ticks into `ExitWatcher`
- `ExitWatcher.on_tick()` feeds M3 builder first, then M1; on M1 close calls `evaluate_exit()`
- State machine: `MONITORING` → `WAITING_REENTRY` → `MONITORING` (if re-entry enabled)
- On exit: calls `ExitExecutor.fill_leg()`, persists to DB, notifies

### `final_<name>.py`
- Orchestrator that loops: execution → monitoring → execution → ...
- **Must** call `_find_open_position()` before each execution loop to skip straight to monitoring
  if a position is already open (restart-safe)
- Handles KeyboardInterrupt cleanly

### `config/execution_settings.yaml` + `config/monitoring_settings.yaml`
- Strategy-specific params only
- Secrets (API key, Telegram) live in `config/global_settings.yaml` — never repeat them here
- Add comment `# OpenAlgo → set in config/global_settings.yaml` where secrets would go

---

## 3. Execution Modes

Four modes controlled by `EXECUTION_MODE` in config:

| Mode | Entry | Exit |
|------|-------|------|
| `MONITOR_ONLY` | Paper (LTP stamp only) | Paper (simulated fill) |
| `ENTRY_EXEC_MONITOR` | Live broker order | Paper exit via monitoring |
| `EXIT_EXEC_MONITOR` | Paper entry | Live broker exit via monitoring |
| `FULL_EXEC` | Live broker order | Live broker exit |

**Rule:** default config always ships with `MONITOR_ONLY`. Change only when ready for live.
The execution and monitoring scripts contain zero `if PAPER else LIVE` branching — they receive
an `EntryExecutor` / `ExitExecutor` object injected from `main()` based on mode.

---

## 4. Config Architecture

### Two-level overlay
`Config.__init__` loads `config/global_settings.yaml` first, then overlays the strategy YAML on top.
Strategy YAMLs must NOT repeat global keys — only add a comment pointing to global.

```yaml
# OpenAlgo → set in config/global_settings.yaml
# Telegram  → set in config/global_settings.yaml
STRATEGY_NAME: "COIL3_INTRADAY_FUT"
```

### Key naming convention
```yaml
STRATEGY_NAME:           string   — human display name
STRATEGY_ID_PREFIX:      string   — prefix for DB run ID (e.g. COIL3 → COIL3_20261027)
EXECUTION_MODE:          enum     — MONITOR_ONLY | ENTRY_EXEC_MONITOR | EXIT_EXEC_MONITOR | FULL_EXEC
UNDERLYING:              string   — CRUDEOIL | NIFTY | BANKNIFTY
EXCHANGE:                string   — MCX | NSE | NFO
INSTRUMENT_TYPE:         string   — futures | options
PRODUCT:                 string   — MIS | NRML
LOT_SIZE:                int
TRADE_DIRECTION:         enum     — LONG | SHORT | BOTH  (directional strategies)
WARMUP_BARS_M1:          int      — 1-min history bars for RSI/MACD/EMA seed
WARMUP_BARS_M3:          int      — 3-min history bars for Supertrend seed (see section 8)
DATABASE_URL:            string   — always sqlite:///logs/strategies.db
LOG_FILE:                string
LOG_LEVEL:               string   — DEBUG | INFO
```

---

## 5. Dual Logger Setup (MANDATORY)

Every strategy script's `main()` must call `setup_logger` for BOTH namespaces:

```python
setup_logger("strategy_framework", log_file=log_file, level=log_level)
setup_logger("strategies",         log_file=log_file, level=log_level)
```

**Why:** `get_logger(__name__)` in strategy scripts resolves to `strategies.*`. Without the second
call, all log output from the strategy script itself is silently swallowed.

---

## 6. Dual-Timeframe Architecture

The canonical pattern for commodity/directional strategies uses two concurrent `CandleBuilder`
instances with different intervals:

```
WebSocket tick
    ├─→ m3_builder.tick(ltp, ts)  → if M3 bar closed → engine.update_m3()
    │                                                 → m3_low_window.update(bar.low)
    │                                                 → m3_high_window.update(bar.high)
    └─→ m1_builder.tick(ltp, ts)  → if M1 bar closed → engine.update_m1()
                                                      → evaluate_entry() / evaluate_exit()
```

**Critical rule:** M3 builder is always ticked FIRST in `on_tick()`. This ensures that when a
1-min candle closes at the same timestamp as a 3-min candle, the M3 indicators are already
updated before the 1-min entry/exit evaluation runs.

**Critical rule:** `m3_low_window` and `m3_high_window` must be updated inside `on_tick()` when
the M3 bar closes — NOT inside `_on_candle_closed()`. The M1 close comes after M3 in the same
tick; the swing windows must already be updated before exit is evaluated.

---

## 7. Indicator Engine Pattern

### Seeding (warm-up)
```python
engine.seed(m1_builder, m3_builder)
```
Processes the full historical window O(N) once. After this, all indicators are warm.

### Incremental update
```python
engine.update_m3(m3_builder)   # called on M3 close — O(1)
engine.update_m1(m1_builder)   # called on M1 close — O(1)
```

### Crossover detection
M3 crossovers (RSI cross, MACD cross) are detected using a `prev_m3_*` snapshot mechanism:
- `snapshot_m3_previous()` is called at the **START** of `update_m3()` before any values change
- Properties `m3_rsi_crossed_below_ema21`, `m3_macd_crossed_above_signal`, etc. compare
  `prev_*` vs current values
- These properties are True only for the 1-min candles evaluated immediately after the M3 close
  where the cross occurred — they auto-reset on the next M3 update

**Rule:** `is_ready` must include `prev_m3_rsi is not None` as a guard. This means the engine
is not ready until the first LIVE M3 candle closes after warm-up (up to 3 minutes). Do not
evaluate entry/exit before `is_ready` returns True.

---

## 8. Supertrend Warm-up: CRITICAL REQUIREMENT

Supertrend is **path-dependent**. Its direction at any point depends on every flip since the
beginning of the data window. Starting from a mid-session warm-up with insufficient bars
produces a completely different (and wrong) Supertrend state.

**Lesson learned (COIL3, 2026-10-27):** Script restarted at 22:49 with only 50 M3 warm-up bars
(going back to 20:21). Seeded ST=9499.57 bullish. Actual session-long ST was 9551 bearish.
A false LONG entry fired at 22:54 based on the wrong ST state.

**Rule:** For MCX crude oil (09:00–23:00 = 840 min ÷ 3 = 280 M3 bars), use:
```yaml
WARMUP_BARS_M3: 300    # covers full MCX session; always use >= session_length_in_bars
```

For NSE (09:15–15:30 = 375 min ÷ 3 = 125 M3 bars), use at minimum 150.

**General rule:** `WARMUP_BARS_M3` must be large enough to cover the full trading session
from open, regardless of when the script is actually started.

---

## 9. Dependency Injection for Executors

Both entry and exit execution use an ABC pattern to decouple PAPER vs LIVE branching from
strategy logic:

```python
class ExitExecutor(ABC):
    @abstractmethod
    def fill_leg(self, leg) -> None: ...

    @property
    @abstractmethod
    def mode_tag(self) -> str: ...

class PaperExitExecutor(ExitExecutor): ...
class LiveExitExecutor(ExitExecutor): ...
```

`ExitWatcher` and `SignalWatcher` receive the executor at construction time. They never branch
on PAPER/LIVE internally. The `main()` function selects the correct executor based on
`EXECUTION_MODE` and injects it. This keeps all broker-specific code in the executor only.

---

## 10. State Machine in Monitoring Scripts

Monitoring scripts with re-entry logic use an explicit string state machine:

```
_watch_state = "MONITORING"
    │
    ├── profit target hit → _book_profit() → _watch_state = "WAITING_REENTRY"
    │                                           │
    │                                           └── evaluate_reentry() fires
    │                                               → reset entry/trail/swing
    │                                               → _watch_state = "MONITORING"
    │
    └── any SL/crossover exit → _fire_exit() → _done.set() → terminate
```

`_on_candle_closed` routes at the top:
```python
if self._watch_state == "WAITING_REENTRY":
    self._check_reentry(close)
    return
# ... normal exit evaluation
```

`_fire_exit()` only sets `_done` — it never executes the trade. Execution always goes through
`execute_exit()` called from `main()` after `wait()` returns.

---

## 11. MultiPhaseTrailSL

Used for directional futures strategies. Replaces simple fixed trail.
Defined in `framework/trailing.py`.

```
Phase 0: SL = entry ∓ initial_sl_pts          (e.g. entry=5000 → SL=4960)
Phase 1: when price moves +phase1_trigger pts  → SL = initial_SL + phase1_sl_add
                                                  (e.g. price=5030 → SL=4990)
Phase 2: when price moves +phase2_trigger pts  → SL = entry + phase2_entry_offset
         thereafter: every step_pts rise       → SL += step_pts
                                                  (e.g. price=5050 → SL=5015,
                                                        price=5055 → SL=5020)
```

Supports both `direction="LONG"` and `direction="SHORT"` (all comparisons reversed for SHORT).

Config keys: `INITIAL_SL_POINTS`, `TRAIL_PHASE1_TRIGGER`, `TRAIL_PHASE1_SL_ADD`,
`TRAIL_PHASE2_TRIGGER`, `TRAIL_PHASE2_ENTRY_OFFSET`, `TRAIL_STEP_POINTS`

**Rule:** always reconstruct `MultiPhaseTrailSL` with `direction=self._direction` — never hardcode
`direction="LONG"`. On re-entry, always create a new instance at the re-entry price.

---

## 12. RollingLowWindow / RollingHighWindow

Track the minimum low / maximum high of the last N closed M3 candles for swing-level exit:
- `RollingLowWindow(n=2)` — LONG swing exit: exit if M1-Close < min low of last N M3 bars
- `RollingHighWindow(n=2)` — SHORT swing exit: exit if M1-Close > max high of last N M3 bars

Both must be seeded during `warmup()` from `m3_builder.lows()[-n:]` / `m3_builder.highs()[-n:]`.
Both must be reset (re-created + re-seeded) whenever re-entry resets the trade state.

Config key: `SWING_3MIN_CANDLES: 2`

---

## 13. DBWriteQueue — Non-Blocking DB Writes

All DB writes from monitoring scripts go through `DBWriteQueue` which runs a background thread
that flushes every `flush_interval` seconds (default 5s). This prevents any DB operation from
blocking the WebSocket tick processing thread.

```python
db_queue.put(save_mtm_snapshot, run_id=..., spot=..., mtm=...)
db_queue.put(save_trade, run_id=..., event_type="EXIT", ...)
```

**Rule:** `save_strategy_run()` is the only DB function called directly (not via queue) because
it must return a `run_id` synchronously before anything else can be queued.

**Rule:** always call `db_queue.stop()` in the finally/cleanup block of `main()`.

---

## 14. Unified Strategy DB

All strategies write to a single SQLite file: `logs/strategies.db`.
Tables: `strategy_runs`, `strategy_legs`, `strategy_mtm_snapshots`, `strategy_trades`,
`strategy_signal_events`, `strategy_adjustments`, `strategy_notifications`.

The `strategy_type` column on `strategy_runs` differentiates strategies:
`"DIRECTIONAL_FUTURES"`, `"CREDIT_SPREAD"`, etc.

`save_strategy_run()` requires `strategy_type=` kwarg — never omit it.

---

## 15. Open Position Guard in `final_<name>.py`

Every orchestrator must check for an already-open position before running execution:

```python
def _find_open_position(exec_config_path: str) -> bool:
    cfg = Config(exec_config_path)
    today_id = f"{cfg.get_str('STRATEGY_ID_PREFIX')}_{now_ist().strftime('%Y%m%d')}"
    with SessionFactory(engine).make() as sess:
        open_run = sess.query(StrategyRun).filter(
            StrategyRun.status == "OPEN",
            StrategyRun.strategy_id == today_id,
        ).first()
    return open_run is not None
```

If found → skip execution, go directly to monitoring. This makes `final_*.py` restart-safe:
if the process crashes mid-monitoring, restarting it automatically resumes monitoring the
existing position without re-entering.

---

## 16. Heartbeat Logging (MANDATORY, Every Candle)

Every monitoring script must log a HEARTBEAT on **every** closed 1-min candle with full
indicator state. No `% N == 0` guard.

```python
logger.info(
    "HEARTBEAT | #%d dir=%s state=%s reentry=%d/%d M1close=%.2f entry=%.2f MTM=%.0f | "
    "SL=%.2f trail_act=%s | "
    "M1[RSI=%.1f EMA21=%.1f ST=%.2f dir=%s] | "
    "M3[RSI=%.1f EMA21=%.1f MACD=%.4f Sig=%.4f ST=%.2f dir=%s swing=%s]",
    ...
)
```

Every execution script must log a CANDLE line on every 1-min close showing each condition
result (✓/✗) so that missed or unexpected signals can be diagnosed immediately from the log.

---

## 17. SignalWatcher Pattern

`SignalWatcher` in execution scripts:
- Holds `_candidates` list based on `TRADE_DIRECTION` config:
  - `"LONG"` → `["LONG"]`
  - `"SHORT"` → `["SHORT"]`
  - `"BOTH"` → `["LONG", "SHORT"]`  (LONG checked first)
- On each M1 close, iterates candidates and calls `evaluate_entry(state, direction=candidate)`
- On first match: captures `(state_snapshot, fired_direction)`, sets `_done` event
- `wait()` returns `(state_snapshot, direction)` or `None` if entry window expired

**Rule:** `TRADE_DIRECTION` config must be explicitly set — never assume a default. Default
in code is `"LONG"` but this must be intentional. If session is bearish, `"LONG"` means
zero signals all day. Set `"BOTH"` for bidirectional strategies.

---

## 18. Recovery Pattern in Monitoring Scripts

Position recovery hierarchy:
1. **DB recovery (primary):** query `strategy_runs` for `status=OPEN` with today's `strategy_id`
2. **Positionbook fallback (live modes only):** call OpenAlgo positionbook API, match by underlying

Paper modes (`MONITOR_ONLY`, `EXIT_EXEC_MONITOR`) skip positionbook fallback — they rely on DB.

The recovered `FutureLeg` or `OptionLeg` carries `entry_price` which seeds `MultiPhaseTrailSL`
and profit target calculations. If recovery fails → script exits cleanly (no position to monitor).

---

## 19. Idempotency Guard in Execution Scripts

Live entry modes (`ENTRY_EXEC_MONITOR`, `FULL_EXEC`) check for an existing open position in the
broker's positionbook before placing any order:

```python
positions = client.get_positions()
for pos in positions:
    if underlying in pos["symbol"] and pos["quantity"] != 0:
        logger.warning("Existing position found — skipping entry")
        sys.exit(0)
```

This prevents double-entry if the execution script is restarted after an order was already filled.

---

## 20. Profit Target + Re-entry State Machine

When a profit target is hit in monitoring:
1. `_book_profit(close)` executes the exit fill and persists to DB
2. If `REENTRY_ENABLED=true` and `reentry_count < MAX_REENTRIES`:
   - leg state is reset (entry_price = booked price, status = OPEN)
   - `MultiPhaseTrailSL` is recreated at booked price
   - swing windows are reset and re-seeded from current M3 history
   - `_watch_state` switches to `"WAITING_REENTRY"`
3. On re-entry signal (`evaluate_reentry()` returns True):
   - entry_price set to re-entry candle close
   - `MultiPhaseTrailSL` recreated at new entry price
   - swing windows reset + re-seeded
   - `_watch_state` switches back to `"MONITORING"`
   - `reentry_count` incremented

Config keys: `PROFIT_TARGET_POINTS`, `REENTRY_ENABLED`, `MAX_REENTRIES`

---

## 21. Naming Conventions

| Item | Convention | Example |
|------|------------|---------|
| Strategy folder | `<asset>_<style>_<direction>_<tf>` | `crudeoil_intraday_future_long_directional_3Min` |
| Script files | `execution_<folder>.py` | `execution_crudeoil_intraday_future_long_directional_3Min.py` |
| Strategy name (config) | `SCREAMING_SNAKE` | `COIL3_INTRADAY_FUT` |
| Strategy ID prefix | short uppercase | `COIL3`, `IDXCS` |
| DB strategy_id | `{PREFIX}_{YYYYMMDD}` | `COIL3_20261027` |
| Exit reason constants | `EXIT_*` | `EXIT_RSI_CROSS_BELOW`, `EXIT_TRAILING_SL` |
| Indicator state fields | `m1_` / `m3_` prefix | `m1_rsi`, `m3_macd_line` |
| Prev-snapshot fields | `prev_m3_` prefix | `prev_m3_rsi`, `prev_m3_macd_signal` |

---

## 22. Indicator Naming

| Indicator | Field naming | Config key |
|-----------|--------------|------------|
| M1 RSI-14 | `m1_rsi` | `RSI_PERIOD` |
| M1 RSI-EMA21 | `m1_rsi_ema_slow` | `RSI_EMA_SLOW_PERIOD` |
| M1 Supertrend | `m1_supertrend`, `m1_supertrend_direction` | `SUPERTREND_PERIOD`, `SUPERTREND_FACTOR` |
| M1 price EMA21 | `m1_ema21_price` | `M1_REENTRY_EMA_FAST` |
| M1 price EMA34 | `m1_ema34_price` | `M1_REENTRY_EMA_SLOW` |
| M3 RSI-14 | `m3_rsi` | `RSI_PERIOD` |
| M3 RSI-EMA8 | `m3_rsi_ema_fast` | `RSI_EMA_FAST_PERIOD` |
| M3 RSI-EMA21 | `m3_rsi_ema_slow` | `RSI_EMA_SLOW_PERIOD` |
| M3 MACD line | `m3_macd_line` | `MACD_FAST`, `MACD_SLOW` |
| M3 MACD signal | `m3_macd_signal` | `MACD_SIGNAL` |
| M3 MACD histogram | computed as `m3_macd_line - m3_macd_signal` | — |
| M3 Supertrend | `m3_supertrend`, `m3_supertrend_direction` | `SUPERTREND_PERIOD`, `SUPERTREND_FACTOR` |
| M3 price EMA8 | `m3_ema8_price` | `M3_PRICE_EMA_FAST` |
| M3 price EMA21 | `m3_ema21_price` | `M3_PRICE_EMA_SLOW` |

Direction convention: `-1` = bullish (price above Supertrend), `+1` = bearish (price below Supertrend)

---

## 23. Run Commands

```bash
# From strategy_framework/ directory:

# Directional 3Min (recommended — loops execution → monitoring)
python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min.final_crudeoil_intraday_future_long_directional_3Min

# Entry only
python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min.execution_crudeoil_intraday_future_long_directional_3Min

# Monitoring only (if execution already ran and position is in DB)
python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min.monitoring_crudeoil_intraday_future_long_directional_3Min

# Credit spread
python -m strategies.credit_spread.index_credit_spread_directional_weekly.final_index_credit_spread_directional_weekly
```

---

## 24. What Goes in `_engine.py` vs Strategy Scripts

| Belongs in `_engine.py` | Belongs in strategy scripts |
|-------------------------|-----------------------------|
| Indicator state dataclass | WebSocket subscription |
| Indicator engine (seed + update) | Config loading |
| Entry / exit / re-entry evaluators (pure) | DB persistence |
| Exit reason constants | Notifier calls |
| RollingLowWindow / RollingHighWindow | EntryExecutor / ExitExecutor |
| Crossover detection properties | Recovery logic |
| Any pure math on indicator values | Heartbeat logging |

**Rule:** if a function in `_engine.py` imports anything other than stdlib, numpy, and
`framework.indicators`, it is in the wrong file.

---

## 25. SOLID Principles Applied

| Principle | How it is applied |
|-----------|-------------------|
| **S** (Single Responsibility) | execution handles entry only; monitoring handles exit only; engine handles indicators only |
| **O** (Open/Closed) | new execution modes added without changing strategy logic — add a new Executor subclass |
| **L** (Liskov Substitution) | `PaperExitExecutor` and `LiveExitExecutor` are interchangeable — watcher never casts |
| **I** (Interface Segregation) | `EntryExecutor` and `ExitExecutor` are separate ABCs with one method each |
| **D** (Dependency Inversion) | `ExitWatcher` depends on `ExitExecutor` ABC, not on `PaperExitExecutor` directly |

---

## 26. Known Issues & Lessons Learned

### Supertrend mid-session restart (2026-10-27, COIL3)
**Problem:** Script restarted at 22:49 with `WARMUP_BARS_M3=50` (back to 20:21). Seeded
ST=9499.57 bullish while actual session ST=9551 bearish. False LONG entry at 22:54.
**Fix:** `WARMUP_BARS_M3=300` — covers full MCX session from 09:00.
**Principle:** path-dependent indicators require session-long history, not just recent N bars.

### TRADE_DIRECTION defaulting to LONG in bearish session (2026-10-27, COIL3)
**Problem:** Config had `TRADE_DIRECTION: "LONG"`. Session was bearish all day. No signals fired.
SHORT conditions (all 4 met at 22:39) were never evaluated because candidates = ["LONG"].
**Fix:** `TRADE_DIRECTION: "BOTH"` for bidirectional strategies. Always verify config matches
the session's expected directional bias before going live.
**Principle:** document and verify all config knobs before each session.

### C5 histogram filter preventing weak MACD-cross entries
**Observation:** At 22:54 on 2026-10-27, MACD just crossed above signal but both lines were
still deep negative. Histogram = +0.2491. Entry fired (old code, no C5).
**Fix:** `abs(histogram) > 0.95` as C5 — filters entries where MACD crossed but has no momentum.
**Principle:** a crossover alone is not a signal; the histogram magnitude confirms conviction.

---

## 27. Checklist Before Building a New Strategy

- [ ] Folder and file names follow section 21 naming convention
- [ ] `_engine.py` contains only pure indicator + evaluator code
- [ ] `execution_*.py` has idempotency guard for live modes
- [ ] `monitoring_*.py` has DB + positionbook recovery
- [ ] `final_*.py` has `_find_open_position()` guard
- [ ] Dual `setup_logger` calls in every `main()`
- [ ] `TRADE_DIRECTION` config key set and verified
- [ ] `WARMUP_BARS_M3` set to cover full session length
- [ ] M3 builder ticked before M1 in `on_tick()`
- [ ] Swing windows updated in `on_tick()` on M3 close (not in `_on_candle_closed`)
- [ ] `MultiPhaseTrailSL` constructed with `direction=` param
- [ ] Heartbeat logged on every M1 candle close (no `% N` guard)
- [ ] Condition-by-condition CANDLE log in execution script
- [ ] Default `EXECUTION_MODE: "MONITOR_ONLY"` in config
- [ ] `DATABASE_URL: "sqlite:///logs/strategies.db"` (shared DB)
- [ ] `strategy_type=` passed to `save_strategy_run()`
- [ ] `db_queue.stop()` called in cleanup
