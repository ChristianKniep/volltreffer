"""Scoring for the betting pool.

Defaults match **teamtip.net**'s scheme so our leaderboard agrees with teamtip's
own ranking. Point values are overridable via env:

  exact score        -> SCORE_EXACT     (default 3)
  correct goal diff  -> SCORE_GOALDIFF  (default 2)   # includes a non-exact correct draw
  correct tendency   -> SCORE_TENDENCY  (default 1)   # right winner/draw, wrong margin
  miss               -> 0

Precedence is exact > goaldiff > tendency > miss.

NB: for imported teamtip ghost members the leaderboard uses teamtip's *own*
points_total (stored on teamtip_members) as the source of truth — see main.py.
This scheme is what we apply to native app users' tips so they score on the same
basis. Keep the two aligned (or override SCORE_* to match a different betgame).
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
