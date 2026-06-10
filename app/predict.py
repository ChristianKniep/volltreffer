"""Match prediction model.

A lightweight Poisson expected-goals model driven by the research-calibrated
power ratings (FIFA April-2026 points scale). Host nations get a fixed
home-advantage bonus. For knockout games the draw mass is reallocated into
"advance" probabilities (extra time / penalties) using an Elo expectation.

This is a transparent statistical model, not a tipster - treat the numbers as
informed guidance, not certainty.
"""
import math

HOST_BONUS = 70          # rating points added for a host nation (USA/MEX/CAN)
BASE_XG = 1.35           # league-average goals per team in a WC match
ELO_DIV = 800            # rating points -> goal-expectation scale
MAXG = 8                 # Poisson grid ceiling


def _pois(k, lam):
    return lam ** k * math.exp(-lam) / math.factorial(k)


def predict(home_rating, away_rating, home_host=False, away_host=False, knockout=False):
    ha = HOST_BONUS if home_host else 0
    aa = HOST_BONUS if away_host else 0
    diff = (home_rating + ha) - (away_rating + aa)

    xh = max(0.2, min(4.2, BASE_XG * (10 ** (diff / ELO_DIV))))
    xa = max(0.2, min(4.2, BASE_XG * (10 ** (-diff / ELO_DIV))))

    p_home = p_draw = p_away = 0.0
    best_p, best_score = -1.0, (0, 0)
    for i in range(MAXG + 1):
        pi = _pois(i, xh)
        for j in range(MAXG + 1):
            p = pi * _pois(j, xa)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if p > best_p:
                best_p, best_score = p, (i, j)

    # knockout: no draws - reallocate by Elo expectation
    e_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    adv_home = p_home + p_draw * e_home
    adv_away = p_away + p_draw * (1 - e_home)

    if knockout:
        fav_home = adv_home >= adv_away
        favored = "home" if fav_home else "away"
        conf = round(100 * max(adv_home, adv_away))
    else:
        outcomes = {"home": p_home, "draw": p_draw, "away": p_away}
        favored = max(outcomes, key=outcomes.get)
        conf = round(100 * outcomes[favored])

    return {
        "xg_home": round(xh, 2), "xg_away": round(xa, 2),
        "p_home": round(100 * p_home), "p_draw": round(100 * p_draw),
        "p_away": round(100 * p_away),
        "adv_home": round(100 * adv_home), "adv_away": round(100 * adv_away),
        "score_home": best_score[0], "score_away": best_score[1],
        "favored": favored, "confidence": conf,
    }


def rationale(home, away, pred, knockout=False):
    """Short, model-derived explanation string."""
    gap = abs(pred["xg_home"] - pred["xg_away"])
    if knockout:
        fav = home if pred["favored"] == "home" else away
        edge = max(pred["adv_home"], pred["adv_away"])
        if edge >= 70:
            return f"{fav} clear favourites to go through ({edge}%)."
        if edge >= 58:
            return f"{fav} edge it, but it's live ({edge}% to advance)."
        return "A coin-flip tie - extra time looks likely."
    fav = home if pred["favored"] == "home" else (away if pred["favored"] == "away" else None)
    if pred["favored"] == "draw":
        return "Evenly matched - honours look like being shared."
    if gap >= 1.4:
        return f"{fav} strongly fancied ({pred['confidence']}%)."
    if gap >= 0.6:
        return f"{fav} favoured ({pred['confidence']}%), but not a gimme."
    return f"Tight one, slight lean to {fav} ({pred['confidence']}%)."
