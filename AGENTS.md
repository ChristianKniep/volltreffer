# AGENTS.md — volltreffer (World Cup 2026 prediction app)

Guidance for AI agents working in this repo. Two distinct jobs live here:

1. **Code** on the FastAPI + SQLite app, Helm chart, and skills (see `HANDOFF_wc2026.md`
   for the full architecture and tribal knowledge — read it first for code work).
2. **Predictions** — revising World Cup match forecasts through the app's API.

This file focuses on **(2)**, the press-informed prediction workflow, because it is
the recurring, human-in-the-loop task. For code conventions, the canonical source is
`HANDOFF_wc2026.md` (e.g. *any static edit ⇒ bump `APP_VERSION` + both `?v=` query
strings; Python edits ⇒ `docker compose restart wc2026`*).

---

## Prediction workflow (press-informed, human-approved)

Goal: revise match scorelines using **real, dated sports-press coverage + results so
far**, never invented news, and write them where the user chose.

### Two write targets — pick deliberately, never guess

| Target | Skill | Token | Scope | When |
|---|---|---|---|---|
| **Personal view** | `skills/my-predictions/` | personal `vt_…` (Settings → Personal API token) | only the requesting user's view; shared model & other players untouched | default — safe, reversible |
| **Shared model** | `skills/update-predictions/` | `AUTOMATION_TOKEN` (server env) | retunes team ratings / pins overrides for **everyone** | only when explicitly asked to change the pool's model |

Both are stdlib-only CLIs. Configure with `VOLLTREFFER_URL` and the matching token in
the shell **before** launching the agent.

```bash
export VOLLTREFFER_URL="https://volltreffer.lab.hvk"   # or http://localhost:8000
export VOLLTREFFER_TOKEN="vt_…"                         # personal token
python skills/my-predictions/my_predictions.py upcoming   # read current view first
python skills/my-predictions/my_predictions.py set G27 --home 3 --away 1 --rationale "…"
python skills/my-predictions/my_predictions.py clear G27   # revert one match to the model
```

Match IDs: `G01`–`G72` (group), `M73`–`M104` (knockout). Predictions exist only for
**upcoming** matches with both teams known (hidden once played).

### The loop that worked

1. **Scope precisely.** Confirm *which* matches and *which* write target with the user.
   "The next round" / "each team's 2nd match" needs computing — don't assume the app's
   fixture order maps 1:1 to matchdays (Groups K/L start later than A–J).
2. **Pull live state** (`/api/state` with the token) to get the **actual results so far**
   and the **model's baseline** scoreline per fixture. Build a per-team form table
   (MD1 result + how it happened).
3. **Gather press per fixture** from real, fetchable sources (see source guide below).
   Fetch **both teams**; capture *manner* (dominant vs lucky, late goals, red cards,
   clean sheets) and **availability** (injuries, suspensions, coach changes).
4. **Recommend per match**: `model baseline → my pick`, 2–3 lines of evidence, an honest
   **confidence** tag (low / medium / high), and whether it's KEEP / CHANGE.
   Change only where there is a real signal; otherwise keep the model.
5. **Get the user's call per match** (or per small batch). Then **write each decision** —
   *including keeps* if the user wants the rationale recorded (a keep is just a `set` at
   the model's score with a rationale).
6. **Verify** by reading `/api/my-predictions` back and showing the diff.

### Honesty rules (non-negotiable)

- **Never fabricate sources, quotes, journalists, or odds.** If you didn't read it, don't
  cite it. State plainly what you used and what you couldn't reach.
- Prefer **durable, verifiable evidence**: actual results from `/api/state`, named
  injuries/suspensions, coach sackings, clear form. Flag low-confidence picks (e.g.
  fixtures whose opener hasn't been played → preview-only, lower confidence).
- The model is transparent, not a tipster. Make **small, justified** moves; keep the
  rationale grounded in what the press actually said.

---

## Sports-news source guide (evidence-based, from live fetches)

What actually returns clean, dated, fetchable content (✅) vs not (⚠️/❌). Prefer
**index/team pages with article links + timestamps**, then fetch the specific
match-report URL for depth.

| Source | Access pattern | Verdict | Notes |
|---|---|---|---|
| **The Guardian** | `theguardian.com/football/<team>` (e.g. `/germany`, `/holland`, `/usfootballteam`) | ✅ **best for analysis** | Dated match reports, **"Team guide"** + **"Experts' Network"** pieces, tactical columns. Diverse, opinionated background — the biggest scoring uplift. Also `/football/world-cup-2026`. |
| **BBC Sport** | `bbc.com/sport/football/teams/<team>` (+ `/scores-fixtures`) | ✅ **best for facts/fixtures** | Reliable results, fixtures, kickoff times, squad/injury news. Heavy on short video clips; lighter analysis. Workhorse for "what happened". |
| **ESPN** | `espn.com/soccer/team/_/...` | ◻️ untested here | Structured fixtures/results; try as a third cross-check. |
| **Flashscore** | `flashscore.com/team/<team>/` | ❌ 404 on guessed paths | Slug scheme not guessable; skip unless given an exact URL. |
| **kicker.de** (German) | `/team/…`, `/wm-2026-…` | ❌ 404 on guessed paths | URL scheme **not** predictable; needs a search-derived article URL, not a team-page guess. |
| **Sportschau** (ARD, German) | `sportschau.de/fussball/fifa-wm-2026` | ⚠️ loads but ~all nav chrome | Landing page is mostly menu; little extractable article text via fetch. Use a **direct article URL** if you have one. |
| **Google search** | `google.com/search?q=…` | ❌ blocked | Returns a JS-redirect stub; don't rely on it for previews/odds. |

**Diversity tip (to improve scoring):** triangulate **Guardian (analysis) + BBC (facts)**
for each fixture; add a **national / local outlet** for the underdog when reachable
(e.g. BBC's own Scotland pages carried manager quotes that flipped a read). National
press is high-value but rarely has a guessable URL — use a direct link if the user
supplies one rather than guessing.

**German press caveat:** kicker/Sportschau were **not** reliably fetchable by guessed
URL in this environment. Don't claim German sources unless you actually extracted
article text (a direct article URL, or user-pasted content). The Guardian + BBC carried
the German-team coverage we needed in English.

### Context-saving pattern for batches

BBC/Guardian pages are large (mostly chrome). When researching many fixtures, **delegate
fetching to a sub-agent** that returns a <400-word distilled summary per team (MD1 result
+ how, key players, availability, one-line verdict) instead of reading raw pages in the
main thread. This is how 24 fixtures were processed without exhausting context. Run
sub-agents in parallel (e.g. two batches of 4 teams).

---

## Scoring — predict to maximise points, not just to be "right"

The pool scores each tip (see `app/score.py`, all env-overridable):

| Outcome | Points (default) |
|---|---|
| Exact score | `SCORE_EXACT` = **4** |
| Correct goal difference (incl. a non-exact correct draw) | `SCORE_GOALDIFF` = **3** |
| Correct tendency (right winner, wrong margin/GD) | `SCORE_TENDENCY` = **2** |
| Miss | 0 |

Implications for picks:
- **Getting the tendency right is the floor** (2 pts) — don't talk yourself into an upset
  without real evidence; protect the winner first.
- **Goal difference is the sweet spot** (3 pts) — when unsure of exact, aim for the most
  likely *margin* (e.g. a clear favourite vs a team that defends well → `2-0`/`1-0` over a
  speculative `4-0`). Several picks this session trimmed model routs (`4-0→3-0`, `4-0→2-0`)
  precisely because the press showed the favourite misfiring or the underdog organised.
- **Exact (4 pts) is the jackpot** — lean on *manner* evidence: a leaky defence + a hot
  striker pairing pushes toward `2-2`/`2-1`; a team that "can't finish" pushes scores down.

---

## Quick reference — API surface used

| Method | Path | Token | Purpose |
|---|---|---|---|
| `GET` | `/api/state` | cookie or any token | results so far, standings, model predictions |
| `GET` | `/api/my-predictions` | personal `vt_` | the user's current overrides |
| `POST` | `/api/match/{id}/my-prediction` | personal `vt_` | set the user's scoreline (`{score_home,score_away,rationale,…}`) |
| `DELETE` | `/api/match/{id}/my-prediction` | personal `vt_` | revert one match to the model |
| `GET/PUT` | `/api/ratings` | `AUTOMATION_TOKEN`/admin | shared-model ratings (the big lever) |
| `POST/DELETE` | `/api/match/{id}/prediction` | `AUTOMATION_TOKEN`/admin | shared-model per-match override |

See `skills/my-predictions/SKILL.md` and `skills/update-predictions/SKILL.md` for the
full field list and the dependency-free clients. `skills/OPENCODE.md` covers running the
skill standalone from an empty directory.
