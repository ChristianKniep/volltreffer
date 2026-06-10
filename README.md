# volltreffer ⚽

Self-hosted **World Cup 2026 prediction pool**. Run your own betting game: a Poisson
model picks every match, players submit their own tips through pluggable betting
backends, and a live leaderboard ranks everyone using the standard German-pool
scoring. Ships as a single `docker compose up`.

> *Volltreffer* — German for "bullseye": an exact-score hit.

## Features

- **Schedule** — all 104 matches in date order, with a kick-off-time tint and a 🔥 fun-factor badge. Times, the day-by-day grouping and the tint are **per-user**: each account picks its time zone (auto-detected from the browser) and rates every hour of its day 1–5 to colour the tint to their own life.
- **Groups & Bracket** — live standings that recompute from results; the knockout tree fills automatically as groups settle and ties are decided.
- **Predictions** — a transparent Poisson expected-goals model gives a scoreline and win/advance probabilities for every match.
- **Multi-user** — real accounts with login. New sign-ups are held pending until an admin approves them and assigns a role (`admin` / `user`).
- **Pluggable betting backends** — each tipping site is a plugin. [teamtip.net](https://teamtip.net) ships built-in; adding another is one self-contained module. Every user stores their own credentials, **encrypted at rest**.
- **Leaderboard** — ranks all players across played matches (exact = 4, goal difference = 3, tendency = 2; all configurable), tie-broken by exacts → goal diffs → tendencies.
- **Hover any match** — after kickoff, a popover shows everyone's tip for that game (and the points each earned once it's finished). Hidden beforehand so nobody can copy.

Stack: **FastAPI** + **SQLite** (in a Docker volume so data survives restarts) and a dependency-free vanilla-JS frontend. Fonts and flags are bundled — it runs fully offline.

## Quick start

```bash
docker compose up --build
# open http://localhost:8000
```

On first boot the database is seeded (48 teams, 104 fixtures, ratings) and an admin
account is created from the env below. **Before exposing this anywhere, change the
defaults.**

```yaml
# docker-compose.yml → environment:
APP_SECRET_KEY: "<long random string>"   # encrypts stored credentials — REQUIRED in prod
ADMIN_USER: "admin"
ADMIN_PASSWORD: "<your password>"
# ALLOW_REGISTRATION: "false"            # invite-only: admins create accounts
# COOKIE_SECURE: "true"                  # set when serving over HTTPS
```

Generate a key: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Changing `APP_SECRET_KEY` later makes already-saved credentials unreadable (users just re-enter them).

To wipe everything: `docker compose down -v` (removes the data volume).

## How players join

1. A player opens the app and clicks **Create one** to register → their account is **pending**.
2. An **admin** sees them in the **Admin** tab and clicks **Approve** (optionally promoting them to admin).
3. The player opens the **Settings** tab and connects a betting backend (e.g. pastes their teamtip bearer token + user/betgame IDs). Credentials are encrypted per-user.
4. Tips sync on every **Update results** click; the **Leaderboard** ranks everyone.

### Connecting teamtip.net

teamtip has no public API, so it needs the bearer JWT from your logged-in session:
DevTools → Network → any `/bg_bet` request → Request Headers → copy the
`authorization` value (`Bearer eyJ…`) into the Settings form along with your
`fk_user` and `fk_betgame`. The token expires periodically — re-paste a fresh one
when sync reports it was rejected. Use only your own account; these are private
endpoints, so mind teamtip's ToS and your pool's fair-play rules.

## Entering results

Both paths immediately recompute standings and advance the bracket:

1. **Manually (authoritative).** Click a match, type the score (penalties for KO ties), *Save result*.
2. **Automatically.** **Update results** runs the configured fetch, then syncs every player's connected backend. Configure a source in `docker-compose.yml`:
   - `FIXTURES_URL` — a JSON feed you control: `[{"id":"G01","home_score":3,"away_score":1}, …]` (`G01`–`G72` group, `M73`–`M104` knockout).
   - `FOOTBALL_DATA_TOKEN` — a free football-data.org token (competition `WC`, aligned by date + team name). Verify 2026 coverage against your plan.

## Scoring

Defined in `app/score.py`, the standard German-pool scheme (overridable via env):

| Outcome | Points | env |
|---|---|---|
| Exact score | 4 | `SCORE_EXACT` |
| Correct goal difference | 3 | `SCORE_GOALDIFF` |
| Correct tendency (right winner/draw) | 2 | `SCORE_TENDENCY` |
| Miss | 0 | — |

A correct non-exact draw (you tipped 1–1, it ended 2–2) counts as goal difference.

## Predictions, and updating them via API

Every match prediction comes from a transparent **Poisson expected-goals model**
(`app/predict.py`) driven by one number per team — a **power rating** on the FIFA
points scale. The rating gap (plus a +70 host bonus) sets each side's expected
goals; a Poisson grid yields win/draw/loss probabilities and the most-likely
scoreline (knockouts reallocate the draw into advance %). It's deterministic and
doesn't learn from results — ratings are the lever.

The model is **agent-writable**. With `AUTOMATION_TOKEN` set, any agent harness can
read the inputs and write them back — adjust ratings (the model recomputes every
prediction, excitement tier and bracket cue coherently) or pin a per-match override:

```bash
export VOLLTREFFER_URL=http://localhost:8000 AUTOMATION_TOKEN=…
python skills/update-predictions/volltreffer_client.py get-ratings
python skills/update-predictions/volltreffer_client.py put-ratings '{"Spain": 1885, "Norway": 1705}'
python skills/update-predictions/volltreffer_client.py set-override G01 --home 2 --away 1 --rationale "Hosts roll."
```

`skills/update-predictions/SKILL.md` is a drop-in skill (with the dependency-free
client) describing the workflow for any agent. Writes use `Authorization: Bearer
<AUTOMATION_TOKEN>`; an admin session cookie also works.

### Per-user predictions (everyone)

Beyond the shared model, **each user can revise the prediction in their own view**
— e.g. after a matchday, feeding in news. Every user generates a **personal API
token** on the Settings tab; the `skills/my-predictions/` skill uses it to read
their view and write per-match overrides (`POST /api/match/{id}/my-prediction`),
private to that account and layered on top of the model (and any shared override).
The Instructions tab walks through installing/configuring the skill with an example
prompt. Personal tokens authenticate as the user via `Authorization: Bearer vt_…`.
For an OpenCode-specific, empty-directory walkthrough see
[`skills/OPENCODE.md`](skills/OPENCODE.md).

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/state?provider=` | teams, standings, matches (prediction, your tip for the active backend) |
| `GET` | `/api/leaderboard` | pool standings across played matches |
| `GET` | `/api/match/{id}/tips` | every player's tip for a game (revealed after kickoff) |
| `GET/PUT` | `/api/ratings` | read / update team power ratings (automation token or admin) |
| `POST/DELETE` | `/api/match/{id}/prediction` | set / clear a shared per-match prediction override |
| `GET/POST/DELETE` | `/api/auth/token` | the caller's personal API token (read / regenerate / revoke) |
| `POST/DELETE` | `/api/match/{id}/my-prediction` | set / clear the caller's own prediction for a match |
| `GET` | `/api/my-predictions` | the caller's per-match overrides |
| `GET/PUT` | `/api/me/prefs` | the caller's time zone + 24-hour 1–5 kick-off slot ratings |
| `POST` | `/api/auth/{register,login,logout}` · `GET /api/auth/me` | accounts |
| `GET` | `/api/admin/users` · `POST …/{id}/approve` · `…/{id}/role` · `DELETE …/{id}` | user management (admin) |
| `GET/PUT/DELETE` | `/api/providers[/{id}/credentials]` · `POST /api/providers/{id}/{test,sync}` | betting backends |
| `POST` | `/api/match/{id}/result` · `/api/match/{id}/tip` | set a result · submit a tip |
| `POST` | `/api/update` · `/api/reset` | fetch+sync · wipe & re-seed (admin) |

## Project layout

```
app/
├─ main.py          # FastAPI app + all routes, /api/state assembly
├─ db.py            # SQLite schema, migrations, idempotent seed
├─ auth.py          # accounts, sessions, role/approval dependencies
├─ crypto.py        # Fernet encryption for stored credentials
├─ score.py         # leaderboard scoring scheme
├─ resolve.py       # standings + knockout resolution
├─ updater.py       # manual + external result fetch
├─ predict.py       # Poisson prediction model
├─ providers/       # betting-backend plugins (base + registry + teamtip)
├─ data/            # teams.json, fixtures.json (seed)
└─ static/          # index.html, app.js, style.css, fonts/, flags/
```

## Adding a betting backend

Drop a module in `app/providers/` that subclasses `BetProvider`, declares its
`credential_fields`, implements `sync_tips` / `submit_tip` / `validate`, and is
decorated with `@register`. It's auto-discovered — no other file changes, and it
appears in the Settings tab automatically.

## Local dev (without Docker)

```bash
pip install -r requirements.txt
python make_seed.py                    # only if you change fixtures/ratings
APP_SECRET_KEY=dev WC_DB=./wc2026.db uvicorn app.main:app --reload --port 8000
```

Note: static files are bind-mounted in Docker (edit + reload), but Python changes
need `docker compose restart wc2026` since uvicorn runs without `--reload` there.

## Notes & caveats

- Predictions are a transparent statistical model, **not betting advice**.
- Standings tie-breakers and the third-place→R32 mapping are documented approximations, isolated in `resolve.py`.
- teamtip endpoints are private/internal and can change without notice; use your own account only.
- Fixtures, venues and kick-off times follow the published list (CEST shown large, US Eastern small). Unofficial — public data.
