"""Scoring for the betting pool.

Defaults match **teamtip.net**'s scheme so our leaderboard agrees with teamtip's
own ranking. Point values are overridable via env:

  exact score        -> SCORE_EXACT     (default 3)
  correct goal diff  -> SCORE_GOALDIFF  (default 2)   # includes a non-exact correct draw
  correct tendency   -> SCORE_TENDENCY  (default 1)   # right winner/draw, wrong margin
  miss               -> 0

Precedence is exact > goaldiff > tendency > miss.

NB: this same scheme scores everyone on the leaderboard — native app users and
imported teamtip ghost members alike (see _leaderboard in main.py). teamtip's
cached points_total is used only as a cross-check/fallback; when it disagrees
with our own computation it has lagged teamtip's ranking (e.g. a corrected
result), so we trust the computed total. Override SCORE_* for a different
betgame (one with extra bonus points would need a ranking re-sync instead).
"""
import os

EXACT = int(os.environ.get("SCORE_EXACT", "3"))
GOALDIFF = int(os.environ.get("SCORE_GOALDIFF", "2"))
TENDENCY = int(os.environ.get("SCORE_TENDENCY", "1"))

LABEL = {"exact": "exact", "goaldiff": "goal diff", "tendency": "tendency", "miss": "miss"}
POINTS = {"exact": EXACT, "goaldiff": GOALDIFF, "tendency": TENDENCY, "miss": 0}


def classify(th, ta, rh, ra):
    """Return 'exact' | 'goaldiff' | 'tendency' | 'miss' for a tip vs a result."""
    if th == rh and ta == ra:
        return "exact"
    td, rd = th - ta, rh - ra
    same_tendency = (td > 0 and rd > 0) or (td < 0 and rd < 0) or (td == 0 and rd == 0)
    if not same_tendency:
        return "miss"
    if td == rd:               # correct goal difference (covers correct non-exact draws)
        return "goaldiff"
    return "tendency"


def points(th, ta, rh, ra):
    return POINTS[classify(th, ta, rh, ra)]


def scheme():
    """Describe the active point scheme for the UI."""
    return {"exact": EXACT, "goaldiff": GOALDIFF, "tendency": TENDENCY,
            "labels": LABEL}
