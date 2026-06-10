"""Scoring for the betting pool.

Standard German prediction-pool scheme (Kicktipp / teamtip default), point
values overridable via env:

  exact score        -> SCORE_EXACT     (default 4)
  correct goal diff  -> SCORE_GOALDIFF  (default 3)   # includes a non-exact correct draw
  correct tendency   -> SCORE_TENDENCY  (default 2)   # right winner/draw, wrong margin
  miss               -> 0

Precedence is exact > goaldiff > tendency > miss.
"""
import os

EXACT = int(os.environ.get("SCORE_EXACT", "4"))
GOALDIFF = int(os.environ.get("SCORE_GOALDIFF", "3"))
TENDENCY = int(os.environ.get("SCORE_TENDENCY", "2"))

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
