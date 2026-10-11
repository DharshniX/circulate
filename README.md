# CIRCULATE prototype (v0.3)

Valuing surplus by what it can still become.

**New in v0.3:** "Sold before it's cut" (Gap Fill) in the engine and the app; a new app with a
Durable / Fresh lane switch and Buyer / Seller / Admin roles; offcut passports with QR codes and
printable labels; a data-quality board; a simulation lab. The old demo page is still at `/legacy`.

One shared core (sellers, orders, compatibility, pooling across sellers,
pickup routing, recovery plan, deals, impact ledger) and one engine per kind
of resource:

| Engine | Resources | Maths | Status |
| --- | --- | --- | --- |
| Flat sheet | Mild steel, stainless, aluminium, plywood, acrylic | 2D MaxRects nesting with kerf, edge margin, grain, defects, machine-bed limits | Working |
| Bar and tube | Rods, bars, pipes, tubes | 1D cutting with kerf, end trims, usable leftovers | Working (one cut length per order) |
| Perishable | Food-business surplus, used cooking oil | Hours of shelf life, storage, FSSAI and RUCO rules, arrival-time check | Working (rules need expert review) |
| Textile | Fabric rolls and remnants | Irregular nesting | Not built |
| Cross-industry by-products | Residues, packaging | Curated compatibility table | Not built |

**Everything in `engine/scenarios.py` is sample data.** Replace it with
measured pieces and real quotes before presenting any result as real.

## Run it on Windows

You need Python 3.10 or newer (python.org; tick "Add python.exe to PATH").

```
cd <the folder that contains requirements.txt>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. This is Python, not Node: `npm` is not used.
If you installed an earlier version, run `pip install -r requirements.txt` again (it adds `segno` for QR codes).

### Let jurors scan QR codes with their phones

The laptop and the phones must be on the same Wi-Fi (a phone hotspot works).

```
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
ipconfig        (find the IPv4 address, for example 192.168.1.23)
```

Open the app at `http://192.168.1.23:8000` (your address, not 127.0.0.1), then go to
Seller > Stock and passports > Print QR labels. The QR codes now point at the laptop's address.
Windows may ask to allow Python through the firewall: allow it on private networks.

## Sign in

Every screen needs an account. Open http://127.0.0.1:8000, choose **I sell surplus**, **I buy materials**
or **Admin**, and sign in with an email and password.

**Demo accounts (DEMO ONLY: these passwords are public, never use them for real data).**
Password for all five: `Circulate@2026` (or whatever `DEMO_PASSWORD` is set to).

| Email | Business | Role |
| --- | --- | --- |
| `sriram@demo.circulate` | Sri Ram Fabricators, Ambattur | Seller (workshop, Durable) |
| `thamarai@demo.circulate` | Thamarai Hotel Kitchen, Egmore | Seller (kitchen, Fresh) |
| `ezhil@demo.circulate` | Ezhil Engineering Works, Ambattur | Buyer (Durable) |
| `anna@demo.circulate` | Anna Nagar Boys' Hostel Mess | Buyer (Fresh) |
| `thozhan@demo.circulate` | Thozhan Food Bank, Saidapet | Buyer (Fresh, food bank) |

On the seller and buyer sign-in pages, tapping a demo business only fills in its email and password;
you still press Sign in.

**Admin.** The admin email is `ADMIN_EMAIL` (default `admin@circulate.local`) and the password is
`ADMIN_PASSWORD`. Locally, if `ADMIN_PASSWORD` is not set, the first start creates the admin with a
random password and prints it once in the console. On a server with no `ADMIN_PASSWORD`, no admin is
created and a warning is logged. Seeding runs at every start and never overwrites a password that was changed.

**New businesses** sign up at `/signup/seller` or `/signup/buyer` and start with empty screens. Their
position on the map comes from their area (Ambattur, Anna Nagar, Egmore, Saidapet, Guindy, Porur,
T Nagar, Velachery, Perungudi or Tambaram; any other area is the city centre), so their distances and
pickup costs are marked "approximate location". There is no email sending: a forgotten password is
reset by the admin under All users, who gives the account holder a temporary password.

**Who sees what.** Sellers see only their own stock, imports, deals and pickups; buyers see only their
own requirements and orders; the admin sees everything, and "View as" shows exactly what one business
sees. Browse surplus stays open: every buyer sees every seller's stock, and Gap Fill and matching use
every buyer's open requirements. A deal is visible to its buyer, its sellers and the admin only.
Passports, QR codes, labels, Share on WhatsApp and the phone registration page (`/register`) stay public.

### Settings (environment variables)

| Variable | What it does |
| --- | --- |
| `SECRET_KEY` | Signs the session cookie. Set a long random value on a server. If missing, a random key is kept in `.secret_key` (git-ignored). Changing it signs everyone out. |
| `ADMIN_EMAIL` | The admin's email. Default `admin@circulate.local`. |
| `ADMIN_PASSWORD` | The admin's password, used only when the admin account is first created. |
| `DEMO_PASSWORD` | Password for the five demo accounts when they are first created. Default `Circulate@2026`. |
| `AUTH_MODE` | `real` (default): email and password. `demo`: the old one-tap demo login, for emergencies only. |

To make a secret key: `python -c "import secrets; print(secrets.token_hex(32))"`.

**Emergency switch.** If sign-in fails during a live demo, set `AUTH_MODE=demo` and restart: the
sign-in pages go back to one tap per demo business and a "Demo login mode" bar shows on every screen.
Set it back to `real` (or remove it) straight after. Sessions last 7 days; five wrong passwords for
one email lock that email for 5 minutes.

Run the tests in a second window:

```
cd <the same folder>
.venv\Scripts\activate
python -m pytest -q
```

On Mac or Linux, use `source .venv/bin/activate` instead.

## What's where

| File | What it does |
| --- | --- |
| `engine/models.py` | Every input, validated: resources and orders for each engine, sellers, transport |
| `engine/materials.py` | Material profiles (density, grain, what happens to it as waste) |
| `engine/nesting.py` | MaxRects nesting with kerf, margin, defects, plus an independent checker |
| `engine/engines/base.py` | The contract every engine follows |
| `engine/engines/flat2d.py` | Flat-sheet engine |
| `engine/engines/linear1d.py` | Bar and tube engine |
| `engine/engines/perishable.py` | Perishable engine |
| `engine/pooling.py` | Fill one order from one or more sellers, any engine; exact search; cheapest vehicle |
| `engine/recovery.py` | Recovery plan: best order of routes within a deadline, salvage last |
| `engine/routes.py` | Builds a seller's routes from engine results |
| `engine/impact.py` | Impact ledger with a counterfactual per material; no CO2 without a cited factor |
| `engine/gapfill.py` | Sold before it's cut: fills the empty space in a cutting plan with neighbours' standing orders, checked by `verify_placements` |
| `engine/scenarios.py` | Sample data for the four demo tabs and the Gap Fill demo |
| `app/main.py` | FastAPI endpoints, accounts and ownership, and the deal life cycle (SQLite) |
| `app/auth.py` | Password hashing (scrypt), signed session cookies and the sign-in throttle |
| `app/static/app.html`, `app.css`, `app.js` | The app: lanes, roles and every screen |
| `app/static/index.html` | The old demo page, served at `/legacy` |
| `tests/` | The tests; `test_auth.py` and `test_ownership.py` cover sign-in and who sees what |

## Adding a new engine

1. Write `engine/engines/<name>.py` with a class that subclasses `Engine` (see `base.py`).
2. Implement `relevant`, `check` (return a reason or None), `capacity`, `take`, `verify`,
   `resource_kg`, `unit_kg`, `buyer_value`, `order_key`, and optionally `route_ok`, `counterfactual`.
3. Add resource and order models with a new `kind` in `models.py` and add them to the `Resource` and `Demand` unions.
4. Register it in `engine/engines/__init__.py`.
5. Add a scenario in `scenarios.py`, a drawing function in `index.html`, and tests.

Pooling, the recovery plan, deals and the ledger work for it without changes.

## Replacing sample data with field data

- Measured pieces: set `label="measured"`.
- Salvage offers: real dated quotes; set `executable=True` only for a firm, current offer.
- Buyer prices and utilisation: from what each buyer tells you.
- Vehicles: real hire quotes in `Transport.vehicles`.
- CO2: pass cited factors to `/api/ledger`; never type a number without a source.

## Research: the Gap Fill simulation

`research/gap_fill_sim.py` asks how much of a part-used sheet's leftover can become
parts that nearby buyers already need, under four policies (scrap it; match after
the cut, ordinary or careful layout; Gap Fill before the cut; Gap Fill plus
reshaping). It uses the same MaxRects packer as the engine. **Everything it
prints is simulated, and every price is an assumption set at the top of the file.**

```
python research/gap_fill_sim.py 1000 research/results.json
python research/summarise.py research/results.json
```

`research/sold_before_cut_example.png` is the demo sheet: 9 base plates
450 x 350 x 6 mm on a 2500 x 1250 sheet, with 43 neighbours' parts nested into the leftover.

## Known limits

- Parts are rectangles (bounding boxes); irregular shapes under-count yield.
- One part type or cut length per order.
- Close probabilities are assumptions until there are real outcomes.
- Straight-line distances on a local grid, not road distances. New businesses are placed from their area: approximate.
- Accounts live in the SQLite file; on a server without a persistent disk they are reset at every restart (the demo accounts come back).
- Food-safety rules are platform rules for a prototype, not legal advice.
