# World Cup 2026 — schedule, predictions & live bracket

A small self-contained web app that turns the WC2026 poster into a living site:

- **Schedule** — all 104 matches in date order, with the kick-off-time tint (green = prime-time in Germany/CEST, red = overnight) and the 🔥 fun-factor badge.
- **Groups** — live standings that recompute from results; top 2 and the 8 best third-placed teams are highlighted.
- **Bracket** — the knockout tree, whose slots **fill automatically** as group standings settle and ties are decided.
- **Predictions** — every match gets a model prediction (score + win/advance probabilities), driven by research-calibrated power ratings.

Stack: a tiny **FastAPI** backend + **SQLite** (stored in a Docker **volume** so results survive restarts) and a dependency-free vanilla-JS frontend. Fonts and flags are bundled, so it runs fully offline.

## Quick start

```bash
docker compose up --build
# open http://localhost:8000
```

That's it. The database is created and seeded (48 teams, 104 fixtures, ratings) on first boot inside the `wc2026-data` volume.

To wipe and start over:

```bash
docker compose down -v        # -v removes the volume (all entered results)
```

## Entering results

Two ways, and both immediately recompute standings and advance the bracket:

1. **Manually (always available).** Click any match (Schedule or Bracket tab), type the score — for knockout ties you can also add a penalty score — and hit *Save result*. This is the authoritative path.
2. **Automatically.** Click **Update results** in the header. This calls `POST /api/update`, which pulls finished games from whichever source you configured (see below). With nothing configured it just tells you so.

### Configuring the auto-fetch source

Set one of these in `docker-compose.yml` (`environment:`) and restart:

- `FIXTURES_URL` — a JSON feed **you** control. A list of objects:
  ```json
  [{"id": "G01", "home_score": 3, "away_score": 1},
   {"id": "M104", "home_score": 1, "away_score": 1, "home_pens": 4, "away_pens": 3}]
  ```
  `id` matches the app's match IDs (`G01`–`G72` for group games, `M73`–`M104` for knockout). This is the easiest path for a cron job or your own scraper.
- `FOOTBALL_DATA_TOKEN` — a free token from football-data.org. The updater reads competition `WC` and aligns finished games to our fixtures by **kick-off date + team name** (with a built-in alias table for name differences). Endpoint availability/coverage for the 2026 edition should be verified against your plan.

## How the bracket resolution works

After every result change, `resolve()` runs and:

1. Recomputes each group's table (points → goal difference → goals for → rating as the tie-breakers — an approximation of FIFA's full tie-break sequence).
2. Once all 12 groups are complete, ranks the twelve third-placed teams and takes the best 8.
3. Fills the Round-of-32 from `Winner X` / `Runner-up X` / `Best 3rd …` slots. The eight "best third" slots are assigned with a **backtracking perfect-match** that always respects each slot's official allowed-group list. FIFA uses a fixed combination table keyed on *which* eight thirds qualify; this produces a valid, plausible assignment, but the exact pairing can differ from FIFA's published table.
4. Propagates `Winner M##` / `Loser M##` references up through R16 → QF → SF → Final and the third-place game (knockout ties resolve on score, then penalties).

## How predictions work

A transparent **Poisson expected-goals model** (`app/predict.py`):

- Each team has a **power rating** on the FIFA-points scale. The published April-2026 FIFA ranking points are used for the top tier (France 1877, Spain 1876, Argentina 1875, England 1826, Portugal 1764, Brazil 1761, Netherlands 1758, Morocco 1756, Belgium 1735, Germany 1730, Croatia 1717, Colombia 1693, Senegal 1689, Mexico 1681, USA 1673, Uruguay 1673, Japan 1660, Switzerland 1649 …). Teams outside the published list use estimates on the same scale, cross-checked against pre-tournament title/advancement odds (Spain & France co-favourites; Norway lifted by Haaland; etc.).
- Host nations (USA, Mexico, Canada) get a fixed home-advantage bonus.
- The rating gap sets each side's expected goals; a Poisson grid gives win/draw/loss probabilities and the single most likely scoreline. For knockout games the draw mass is reallocated into "advance" probabilities (extra time / penalties).

It's informed guidance, **not certainty** — group-stage form routinely upends pre-tournament ratings, and knockout predictions only appear once both teams are known. To re-tune, edit the `RATING` map in `make_seed.py` (then re-seed) or the model constants in `app/predict.py`.

## Seeing your teamtip tips next to the model

If you play in a [teamtip.net](https://teamtip.net) round, the app can show **your actual submitted tips** under each match — flagged amber when they differ from the model's prediction (e.g. after you changed a tip manually) and green when they agree.

Set these in `docker-compose.yml` and restart:

```yaml
TEAMTIP_TOKEN: "Bearer eyJ..."   # DevTools -> Network -> any /bg_bet request -> authorization header
TEAMTIP_USER: "454596"           # your fk_user
TEAMTIP_BETGAME: "150936"        # your round id
```

Tips are pulled on every **Update results** click (or via `POST /api/teamtip/sync`) and stored in the `tips` table, so they stay visible even after the token expires — the JWT only lasts a week or two, after which the sync reports "token rejected" until you paste a fresh one. To push the model's tips *into* your round, use `tools/teamtip_submit.py` (see its header).

## API

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | the web app |
| `GET`  | `/api/state` | everything: teams, group standings, all matches (with prediction, excitement, time tint, resolved teams, scores) |
| `POST` | `/api/match/{id}/result` | body `{home_score, away_score, home_pens?, away_pens?}` — set a result, recompute |
| `POST` | `/api/update` | run the configured fetch + teamtip tip sync, then recompute; returns a summary |
| `POST` | `/api/teamtip/sync` | pull your tips from your teamtip round only |
| `POST` | `/api/reset` | wipe and re-seed (dev) |

## Project layout

```
wc2026-app/
├─ docker-compose.yml      # service + named volume wc2026-data:/data
├─ Dockerfile
├─ requirements.txt
├─ make_seed.py            # regenerates app/data/*.json (fixtures + ratings)
└─ app/
   ├─ main.py              # FastAPI app, /api/state assembly, excitement+time tint
   ├─ db.py                # SQLite schema + idempotent seed  (WC_DB, default /data/wc2026.db)
   ├─ resolve.py           # standings + knockout resolution
   ├─ updater.py           # manual + external result fetch
   ├─ predict.py           # Poisson prediction model
   ├─ data/                # teams.json, fixtures.json (seed)
   └─ static/              # index.html, app.js, style.css, fonts/, flags/
```

## Local dev (without Docker)

```bash
pip install -r requirements.txt
python make_seed.py                       # only needed if you change fixtures/ratings
WC_DB=./wc2026.db uvicorn app.main:app --reload --port 8000
```

## Notes & caveats

- Schedule, venues and kick-off times follow the published fixture list; times are shown in CEST (large) with US Eastern (small) as on the poster. Unofficial — public fixture data.
- The third-place→R32 mapping and the standings tie-breakers are documented approximations; both are isolated in `resolve.py` if you want to drop in the exact FIFA tables.
- Predictions are a statistical model, not betting advice.
