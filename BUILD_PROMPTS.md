# CIRCULATE polish prompts for Claude Code (v0.3)

The engine, Gap Fill, lanes, roles, passports and admin screens are already built and tested (50 tests).
These prompts only polish. Paste one at a time. After each: run `python -m pytest -q`, open the app, look, then commit.
Model: Opus 5.5 at high effort for prompts 1 to 3; Sonnet 5.5 at medium is enough for 4 and 5.

## 1. Check it runs (5 minutes)

```
Read CLAUDE.md. Install requirements, run the tests, start the app, and open every screen in both lanes and all three roles. List anything broken or ugly, in order of how bad it looks in a live demo. Change nothing yet.
```

## 2. Make it look finished (about 1 hour)

```
Polish the look of app/static/app.css and app.js without changing what anything computes. Keep the design tokens in app.css. Goals: consistent spacing on an 8px grid, aligned card edges, clear hierarchy on every screen (one main number per card), hover and focus states on every control, smooth but short transitions (respect prefers-reduced-motion), and a clean dark theme. Download the three Google fonts (Barlow Semi Condensed, IBM Plex Sans, IBM Plex Mono) into app/static/fonts and load them locally so the app works offline. Then take screenshots at 1366px and 400px wide in light and dark and fix anything that overflows or is hard to read.
```

## 3. Demo mode for the jury (about 45 minutes)

```
Add a guided demo: a "Start demo" button in the top bar that walks through five steps with a short caption each:
1) Seller > Sold before it's cut (show the before and after sheet), 2) press Decline on the top order and show the re-plan,
3) Buyer > Order and match for Workshop W, accept the order, 4) Seller > Stock and passports, open one passport,
5) Admin > Data quality. Keyboard: right arrow = next step, Esc = leave the demo. No new numbers; use what the engine returns.
```

## 4. Replace sample data with your real job (when you have it)

```
I measured a real job. Add it as a new Gap Fill demo in engine/scenarios.py with label "measured" for the values I measured and "sample" for the rest:
sheet <length x width x thickness, grade>, job <part size and quantity>, scrap quote Rs <price>/kg from <dealer> on <date>, new plate Rs <price>/kg from <supplier> on <date>.
Add a switch on the Sold before it's cut screen between "Sample demo" and "Real job". Update the data-quality board. Add tests.
```

## 5. Freeze (about 30 minutes)

```
Prepare for a live demo with no internet. Make sure nothing loads from a CDN. Add a start_demo.bat that activates the venv and starts the app on 0.0.0.0:8000. Run all tests. Take screenshots of every screen and save them in demo_screens/ as a backup.
```
