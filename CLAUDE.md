# CIRCULATE: project rules for Claude Code

CIRCULATE is a surplus exchange. One matching engine turns a business's surplus into parts and lots another business needs. Two lanes share it: **Durable** (steel, aluminium, plywood, bars and tubes) and **Fresh** (food and other perishables, counted in hours). Three demo roles sit on top: **Buyer, Seller, Admin** (a role switch, no login).

The main idea, to be shown first in every demo: **"Sold Before It's Cut"** (Gap Fill). Before a workshop cuts its own job, the empty space in its cutting plan is filled with parts nearby buyers already ordered. The engine version is `engine/gapfill.py` (screen: Seller > Sold before it's cut). The research simulation is `research/gap_fill_sim.py`.

## Run and test

```
python -m venv .venv
.venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m uvicorn app.main:app --reload     # http://127.0.0.1:8000
python -m pytest -q                          # all tests must stay green
```

## Honesty rules (never break these)

1. Every figure carries a label: **Measured**, **Sample**, **Assumption**, **Simulated** or **Verified**. Sample data shows an amber "Sample" chip.
2. A simulated deal is never counted as verified, even with evidence.
3. CO2 appears only when the admin has entered a cited emission factor. Never type a CO2 number.
4. No landfill claim for steel offcuts (they are recycled anyway). The claim is reuse over recycling.
5. Every scrap or plate price shows its source and date.
6. No invented statistics, no "first in the world" claims, no AI API calls, no blockchain, no real payments.
7. Barter, exponential price decay for food, and chemicals are out of scope.
8. Food rules are platform rules that still need expert review. Say so in the UI.

## Code map

- `engine/gapfill.py`: Sold before it's cut. Every plan passes `verify_placements`.
- `engine/nesting.py`: MaxRects nesting and `verify_placements` (independent checker).
- `engine/engines/`: one engine per goods type (`flat2d`, `linear1d`, `perishable`). New engines subclass `Engine` in `base.py` and register in `__init__.py`.
- `engine/pooling.py`, `recovery.py`, `routes.py`, `impact.py`: shared by every engine.
- `engine/scenarios.py`: SAMPLE data. Never present it as real.
- `app/main.py`: FastAPI endpoints and the deal life cycle in SQLite.
- `app/static/app.html`, `app.css`, `app.js`: the app (lanes, roles, all screens). Design tokens live at the top of `app.css`.
- `app/static/index.html`: the old demo page, served at `/legacy`. Leave it alone.

## Design system (the app must look finished, not like a prototype)

- Reference look: `research/control_room.html`. Calm, dense, professional: slate neutrals, blue for Durable, green for Fresh, one amber accent for the highlighted result.
- Fonts: Barlow Semi Condensed (headings), IBM Plex Sans (body), IBM Plex Mono (numbers, `font-variant-numeric: tabular-nums`). Bundle the font files locally so the demo works offline.
- All colours are CSS variables, with light and dark themes. 8px spacing grid. One radius for cards, one for controls.
- Every screen needs: a real empty state, a loading state, an error state that says how to fix it, and a visible keyboard focus.
- Left rail navigation, a top bar with the Lane switch and Role switch, tables that scroll inside their card on phones, no horizontal page scroll, no emoji.
- Show the reason whenever the engine refuses something. That is a feature, not an error.

## How to work

- Small steps. After each step run `python -m pytest -q`, then start the app and look at it.
- Do not break the existing 50 tests. Add tests for everything new.
- Commit after each finished part with a clear message, so a bad step can be undone.
- After each part, say in three lines what changed and what to check. Ask before any large refactor.
- No npm unless asked. The backend is Python; the front end is plain HTML, CSS and JavaScript served by FastAPI.
