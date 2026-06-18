"""Updating match results.

Two paths:
  * set_result()  - authoritative manual entry (always works, used by the UI).
  * fetch_remote() - best-effort pull from an external source so the bracket
                     can self-update. Two adapters:
        FIXTURES_URL        -> a simple JSON list [{id, home_score, away_score,
                               home_pens?, away_pens?}]  (great for cron/automation)
        FOOTBALL_DATA_TOKEN -> football-data.org v4 (competition WC); matches are
                               aligned by kickoff date + team name.
After any change we re-run resolve() so knockout slots advance.
"""
import datetime
import json
import os
import urllib.error
import urllib.request

from . import resolve as R


def _finish(conn, mid, hs, as_, hp=None, ap=None):
    m = conn.execute("SELECT stage FROM matches WHERE id=?", (mid,)).fetchone()
    if not m:
        return False
    conn.execute(
        """UPDATE matches SET home_score=?, away_score=?, home_pens=?, away_pens=?,
           status='finished' WHERE id=?""", (hs, as_, hp, ap, mid))
    return True


def set_result(conn, mid, hs, as_, hp=None, ap=None):
    ok = _finish(conn, mid, hs, as_, hp, ap)
    if ok:
        _stamp(conn)
        R.resolve(conn)
    return ok


def _stamp(conn):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_update',?)",
                 (datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",))
    conn.commit()


def _norm(s):
    return (s or "").strip().lower().replace("&", "and")


# common name differences between feeds and our dataset
ALIAS = {
    "united states": "usa", "usa": "usa", "south korea": "south korea",
    "korea republic": "south korea", "ivory coast": "ivory coast",
    "cote d'ivoire": "ivory coast", "côte d'ivoire": "ivory coast",
    "czech republic": "czechia", "turkiye": "türkiye", "turkey": "türkiye",
    "bosnia and herzegovina": "bosnia & herzegovina", "cape verde": "cape verde",
    "cabo verde": "cape verde", "dr congo": "dr congo",
    "congo dr": "dr congo", "curacao": "curaçao",
}


def _canon(name):
    n = _norm(name)
    return ALIAS.get(n, n)


def fetch_remote(conn):
    summary = {"source": None, "updated": 0, "skipped": 0, "errors": []}
    url = os.environ.get("FIXTURES_URL")
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    try:
        if url:
            summary["source"] = "FIXTURES_URL"
            data = json.loads(_http(url))
            for row in data:
                if _finish(conn, row["id"], row["home_score"], row["away_score"],
                           row.get("home_pens"), row.get("away_pens")):
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
        elif token:
            summary["source"] = "football-data.org"
            summary.update(_fetch_football_data(conn, token))
        else:
            summary["source"] = "none"
            summary["errors"].append(
                "No FIXTURES_URL or FOOTBALL_DATA_TOKEN set; nothing to fetch. "
                "Enter results manually or configure a source.")
    except Exception as e:  # network/parse/etc - stay graceful
        summary["errors"].append(f"{type(e).__name__}: {e}")

    if summary["updated"]:
        _stamp(conn)
        R.resolve(conn)
    else:
        _stamp(conn)
    return summary


def _http(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def _is_429(exc):
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 429


def _fetch_football_data(conn, token):
    out = {"updated": 0, "skipped": 0, "goals": 0, "goals_pending": 0,
           "rate_limited": False, "errors": []}
    raw = _http("https://api.football-data.org/v4/competitions/WC/matches",
                headers={"X-Auth-Token": token})
    payload = json.loads(raw)
    # index our matches by (date, frozenset(home,away)) for alignment
    idx = {}
    for m in conn.execute("SELECT id,home_team,away_team,kickoff_et FROM matches"):
        if not m["home_team"] or not m["away_team"]:
            continue
        d = m["kickoff_et"][:10]
        key = (d, frozenset((_canon(m["home_team"]), _canon(m["away_team"]))))
        idx[key] = m["id"]
    # matches whose goal timeline we already have -> never re-fetch (idempotent,
    # keeps us well under football-data's free 10 req/min limit on re-runs)
    from . import db
    have_goals = db.matches_with_goals(conn)
    stop_goals = False   # set once we hit a 429; skip remaining goal-pulls
    for fx in payload.get("matches", []):
        if fx.get("status") != "FINISHED":
            continue
        ht = fx.get("homeTeam", {}).get("name"); at = fx.get("awayTeam", {}).get("name")
        ft = fx.get("score", {}).get("fullTime", {})
        d = (fx.get("utcDate") or "")[:10]
        key = (d, frozenset((_canon(ht), _canon(at))))
        mid = idx.get(key)
        if mid and ft.get("home") is not None:
            pens = fx.get("score", {}).get("penalties", {})
            if _finish(conn, mid, ft["home"], ft["away"],
                       pens.get("home"), pens.get("away")):
                out["updated"] += 1
            # goals: skip if we already have them, or if we've been rate-limited
            if mid in have_goals:
                continue
            if stop_goals:
                out["goals_pending"] += 1
                continue
            try:
                g = _fetch_match_goals(conn, token, fx.get("id"), mid,
                                       _canon(ht), _canon(at))
                out["goals"] += g
            except Exception as e:  # noqa: BLE001 - goals are optional
                if _is_429(e):
                    # back off: stop pulling goals this run, resume on the next
                    stop_goals = True
                    out["rate_limited"] = True
                    out["goals_pending"] += 1
                else:
                    out["errors"].append(f"goals {mid}: {type(e).__name__}")
        else:
            out["skipped"] += 1
    if out["rate_limited"]:
        out["errors"].append(
            f"football-data rate limit hit — {out['goals_pending']} match(es) "
            "still need goal timelines. Scores are saved; click Update again "
            "in a minute to fetch the rest.")
    return out


def _fetch_match_goals(conn, token, fd_match_id, mid, home_canon, away_canon):
    """Fetch a single match's goal events from football-data and store them.
    Only persists if the feed actually exposes goals[] with minutes (paid tiers);
    otherwise leaves any existing/manual goals untouched."""
    from . import db
    if not fd_match_id:
        return 0
    raw = _http(f"https://api.football-data.org/v4/matches/{fd_match_id}",
                headers={"X-Auth-Token": token})
    detail = json.loads(raw)
    home_name = _canon(detail.get("homeTeam", {}).get("name"))
    events = detail.get("goals") or []
    parsed = []
    for ev in events:
        team = _canon((ev.get("team") or {}).get("name"))
        side = "home" if team == home_name else "away"
        minute = ev.get("minute")
        if isinstance(minute, str):
            try:
                minute = int(minute.split("+")[0])
            except ValueError:
                minute = None
        scorer = (ev.get("scorer") or {}).get("name")
        parsed.append({"side": side, "minute": minute, "scorer": scorer})
    if not parsed:
        return 0  # tier doesn't expose goals — don't clobber manual data
    return db.set_match_goals(conn, mid, parsed, source="football-data")
