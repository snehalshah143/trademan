## TradeMan Project Context & Safety Rules
### Always follow these rules before making ANY changes

---

### Project Overview
TradeMan is an algorithmic trading application with:
- Backend: Python (Flask/FastAPI), connects to OpenAlgo broker API
- Frontend: React + TypeScript
- Database: SQLite
- Broker Integration: OpenAlgo (REST: http://127.0.0.1:5000, WS: ws://127.0.0.1:8765)

---

### Step 1 — Read Before You Write
Before touching any file, read and understand:
- The file you are about to modify
- All files that import or depend on that file
- All files that the modified component renders inside

For Strategy Builder specifically, always read:
- StrategyBuilder.tsx (or .jsx)
- PositionsTab.tsx
- InstrumentHeader.tsx
- OptionChain.tsx
- Relevant backend service file if touching API

---

### Step 2 — Existing Features Checklist
After every change, verify these existing features are NOT broken:

**Strategy Builder — Positions Tab:**
- [ ] Instrument header visible with: name, lot size, LTP, change%, 
      FUT expiry, FUT price, B button, S button, Clear button
- [ ] B/S buttons open inline leg form (not a dialog)
- [ ] Inline leg form has: Strike, CE/PE/FUT, Expiry, Lots, LTP, 
      Product (MIS/NRML), Add/Cancel buttons
- [ ] Confirming leg adds a row to the legs table instantly
- [ ] Legs table shows: #, Side, Instrument, Expiry, Lots, 
      Entry Price, LTP, P&L, Product, Remove button
- [ ] Remove button (✕) removes that leg from the table
- [ ] Clear button removes all legs
- [ ] MTM footer shows: leg count, total strategy MTM, total lots
- [ ] Prebuilt strategy cards visible when no legs exist
- [ ] Clicking a prebuilt card auto-populates its legs

**Strategy Builder — Option Chain Tab:**
- [ ] Exchange/Underlying/Expiry/Strike Count selectors work
- [ ] Option chain table renders CE and PE sides with ATM highlighted
- [ ] Clicking a strike adds it as a leg to Positions tab
- [ ] Expiry format displays as "DD MMM" (e.g. "27 Mar") not "2026-0"

**Settings Page:**
- [ ] Adapter type toggle (Mock / OpenAlgo) works
- [ ] OpenAlgo Host, WS Host, API Key fields editable
- [ ] Test Connection button calls backend and shows 
      success/failed status
- [ ] Save button persists config
- [ ] System Status shows Backend and Broker connectivity

**General:**
- [ ] Navigation between pages works (Positions, Strategy Builder, 
      Order Book, Settings)
- [ ] No console errors introduced
- [ ] API calls use the OpenAlgo host + API key from saved settings, 
      not hardcoded values

---

### Step 3 — Change Rules
1. Do NOT remove any existing props, state variables, or handlers 
   unless explicitly asked
2. Do NOT restructure component hierarchy unless explicitly asked
3. Do NOT rename existing functions, components or API endpoints
4. If a new feature requires modifying an existing component, 
   ADD to it — do not rewrite it from scratch
5. If you must refactor, list exactly what you are changing and why

---

### Step 4 — After Every Change, Report
At the end of your response always provide:

**Files Modified:**
- list every file you changed

**Files Read (but not changed):**
- list every file you read for context

**Existing features verified:**
- list which checklist items above you confirmed still work

**Known risks:**
- list anything you are unsure about that may need manual testing