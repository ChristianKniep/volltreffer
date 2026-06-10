---
name: my-predictions
description: Update a volltreffer user's own World Cup match predictions. Use when the user wants to review fixtures, discuss a match, and revise their predicted scoreline in their personal view — e.g. after a matchday, factoring in news, injuries, form, or odds. Changes are private to the user and never touch the shared model or betting tips.
---

# my-predictions

In volltreffer every match shows a model-generated prediction. This skill lets a
**user revise that prediction in their own view** — typically after a matchday,
once they've gathered news — without affecting the shared model or anyone else.

## Setup

Needs the running app and the user's **personal API token** (volltreffer →
Settings tab → *Personal API token* → Generate). Configure:

- `VOLLTREFFER_URL` — base URL (default `http://localhost:8000`)
- `VOLLTREFFER_TOKEN` — the user's token (starts with `vt_`)

A stdlib-only client sits next to this file: `my_predictions.py` (library + CLI).
Or call the endpoints directly with any HTTP client, sending
`Authorization: Bearer <VOLLTREFFER_TOKEN>`.

## Workflow

1. **Read the user's current view.**
   ```bash
   python my_predictions.py upcoming   # predictable matches + current prediction + whether it's already yours
   python my_predictions.py mine       # the user's existing overrides
   ```
2. **Discuss, match by match.** Use whatever the user brings — match reports,
   injury/squad news, form, must-win context, odds — and the current prediction
   from step 1. Propose a revised scoreline with a short, honest rationale. Ask
   before writing unless the user said to apply automatically.
3. **Write the agreed prediction** (per match the user approves):
   ```bash
   python my_predictions.py set G12 --home 2 --away 1 --rationale "Star striker back; opponent rotating."
   ```
   Revert a match to the model any time:
   ```bash
   python my_predictions.py clear G12
   ```
4. **Confirm.** Re-read `upcoming` / `mine` and show the user the diff.

Match IDs are `G01`–`G72` (group) and `M73`–`M104` (knockout). Predictions only
exist for upcoming matches with both teams known (they're hidden once played).

## API

| Method | Path | Body | Notes |
|---|---|---|---|
| `GET` | `/api/state` | — | full view; each match's `prediction` reflects your overrides (`override_source:"you"`) |
| `GET` | `/api/my-predictions` | — | `{match_id: {fields…}}` you've set |
| `POST` | `/api/match/{id}/my-prediction` | any of `score_home, score_away, p_home, p_draw, p_away, adv_home, adv_away, favored, confidence, rationale` | sets your prediction for that match |
| `DELETE` | `/api/match/{id}/my-prediction` | — | reverts to the model |

All use `Authorization: Bearer <token>`.

## Guidance

- This is the user's personal forecast — keep rationales grounded in the evidence
  they provide; don't invent news.
- Usually you only need `score_home` / `score_away` (and a `rationale`). The model
  keeps the rest unless you override it too.
- Suggest re-running after each matchday, when fresh coverage is available.
