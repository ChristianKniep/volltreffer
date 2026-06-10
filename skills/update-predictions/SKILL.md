---
name: update-volltreffer-predictions
description: Update the World Cup prediction model in a volltreffer instance — adjust team power ratings (the main lever, which recomputes every match prediction) or pin a per-match scoreline override. Use when asked to retune predictions, react to results/form/injuries/odds, or set a specific match forecast.
---

# Update volltreffer predictions

volltreffer predicts every World Cup 2026 match with a Poisson model driven by one
number per team: a **power rating** on the FIFA-points scale (e.g. France ≈ 1877).
Bigger rating gap → more expected goals → win/draw/loss probabilities and a
most-likely scoreline. Change the ratings and *all* predictions, the 🔥 excitement
tiers, and the bracket's favoured-team highlighting move together. You can also
**override** a single match's prediction directly.

## Setup

This skill talks to the running app's HTTP API. You need:

- `VOLLTREFFER_URL` — base URL (default `http://localhost:8000`)
- `AUTOMATION_TOKEN` — must equal the server's `AUTOMATION_TOKEN` env var

A dependency-free client lives next to this file: `volltreffer_client.py` (stdlib
only). Use it as a library or CLI, or call the endpoints directly with any HTTP
client. All write endpoints accept `Authorization: Bearer <AUTOMATION_TOKEN>`.

## Workflow

1. **Read the current inputs.**
   ```bash
   python volltreffer_client.py get-ratings   # {ratings:[{name,grp,iso,rating,host}], model, overrides}
   python volltreffer_client.py get-state     # fixtures, results so far, current predictions
   ```
2. **Decide new values.** Typical signals: results/form so far (from `get-state`),
   injuries or squad news, or market odds. Keep ratings on the same scale as the
   existing ones (roughly 1330–1880; hosts already get a built-in +70). Move
   ratings *relative to each other* — only the gap between two teams matters.
3. **Write ratings back** (the preferred lever — stays coherent across the app):
   ```bash
   python volltreffer_client.py put-ratings '{"Norway": 1705, "Spain": 1885}'
   ```
   Unknown team names are returned in `unknown`, not created.
4. **Or override one match** when you want an explicit forecast regardless of
   ratings (e.g. a known rotation/dead-rubber):
   ```bash
   python volltreffer_client.py set-override G01 --home 2 --away 1 --rationale "Hosts rotate but win."
   python volltreffer_client.py clear-override G01    # revert to the model
   ```

Changes apply immediately — the next page load / `GET /api/state` reflects them.
Predictions are hidden once a match is finished, so only upcoming matches show them.

## API reference

| Method | Path | Body | Notes |
|---|---|---|---|
| `GET` | `/api/ratings` | — | current ratings + model constants + active overrides |
| `PUT` | `/api/ratings` | `{"ratings": {"Spain": 1885, ...}}` | updates existing teams; returns `updated` / `unknown` |
| `POST` | `/api/match/{id}/prediction` | any of `score_home, score_away, p_home, p_draw, p_away, adv_home, adv_away, favored, confidence, rationale` | merges over the model output; sets `overridden=true` |
| `DELETE` | `/api/match/{id}/prediction` | — | removes the override |
| `GET` | `/api/state` | — | fixtures, results, standings, predictions (read-only; cookie or token) |

Match IDs are `G01`–`G72` (group) and `M73`–`M104` (knockout).

## Guidance

- Prefer **ratings** over per-match overrides — overrides can contradict the
  excitement/bracket cues that still derive from ratings.
- Make small, justified moves and keep a note of *why* (use the `rationale` field
  on overrides). This is a transparent model, not a tipster.
- Re-read `get-state` after writing to confirm the new prediction looks sane.
