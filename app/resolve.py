"""Standings computation and knockout-bracket resolution.

resolve() is idempotent: it recomputes group standings from finished group
matches, fills the Round-of-32 from group winners / runners-up / best third
placers, and propagates knockout winners up the bracket. Call it after any
result change.

Third-place assignment: FIFA uses a fixed combination table keyed on *which*
eight of the twelve third-placed teams qualify. We approximate it with a
deterministic greedy fill - each "Best 3rd" slot lists the groups it may draw
from, and we assign the highest-ranked qualified third from an allowed group.
The slot's allowed-group list is the official one, so the result is always
valid; only the exact pairing can differ from FIFA's table.
"""
import re

GROUPS = list("ABCDEFGHIJKL")


def _team_rating(conn, name):
    r = conn.execute("SELECT rating FROM teams WHERE name=?", (name,)).fetchone()
    return r["rating"] if r else 1450


def group_table(conn, g):
    teams = [r["name"] for r in conn.execute(
        "SELECT name FROM teams WHERE grp=? ORDER BY name", (g,))]
    st = {t: dict(team=t, P=0, W=0, D=0, L=0, GF=0, GA=0, GD=0, Pts=0) for t in teams}
    rows = conn.execute(
        "SELECT * FROM matches WHERE stage='group' AND grp=? AND status='finished'", (g,))
    for m in rows:
        h, a, hs, as_ = m["home_team"], m["away_team"], m["home_score"], m["away_score"]
        if h not in st or a not in st or hs is None:
            continue
        st[h]["P"] += 1; st[a]["P"] += 1
        st[h]["GF"] += hs; st[h]["GA"] += as_
        st[a]["GF"] += as_; st[a]["GA"] += hs
        if hs > as_:
            st[h]["W"] += 1; st[a]["L"] += 1; st[h]["Pts"] += 3
        elif hs < as_:
            st[a]["W"] += 1; st[h]["L"] += 1; st[a]["Pts"] += 3
        else:
            st[h]["D"] += 1; st[a]["D"] += 1; st[h]["Pts"] += 1; st[a]["Pts"] += 1
    for t in st.values():
        t["GD"] = t["GF"] - t["GA"]
    # FIFA tiebreakers approximated: points, goal difference, goals for, rating
    ordered = sorted(st.values(),
                     key=lambda t: (t["Pts"], t["GD"], t["GF"], _team_rating(conn, t["team"])),
                     reverse=True)
    for i, t in enumerate(ordered):
        t["rank"] = i + 1
    return ordered


def group_complete(conn, g):
    n = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE stage='group' AND grp=? AND status='finished'",
        (g,)).fetchone()["c"]
    return n == 6


def ko_winner(conn, mid):
    m = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    if not m or m["status"] != "finished" or m["home_team"] is None:
        return None
    if m["home_score"] > m["away_score"]:
        return m["home_team"]
    if m["away_score"] > m["home_score"]:
        return m["away_team"]
    # tie -> penalties
    hp, ap = m["home_pens"], m["away_pens"]
    if hp is not None and ap is not None and hp != ap:
        return m["home_team"] if hp > ap else m["away_team"]
    return None


def ko_loser(conn, mid):
    w = ko_winner(conn, mid)
    if not w:
        return None
    m = conn.execute("SELECT home_team,away_team FROM matches WHERE id=?", (mid,)).fetchone()
    return m["away_team"] if w == m["home_team"] else m["home_team"]


def _best_thirds(conn):
    """Return ranked list of qualified third-placed teams (best 8) as
    list of (group, team) plus a quick group->team map."""
    thirds = []
    for g in GROUPS:
        if not group_complete(conn, g):
            return None  # need all groups complete for the official mechanism
        tbl = group_table(conn, g)
        third = tbl[2]
        thirds.append(dict(group=g, team=third["team"], Pts=third["Pts"],
                           GD=third["GD"], GF=third["GF"],
                           rating=_team_rating(conn, third["team"])))
    thirds.sort(key=lambda t: (t["Pts"], t["GD"], t["GF"], t["rating"]), reverse=True)
    return thirds[:8]


def _assign_thirds(conn):
    """Assign the eight best third-placed teams to the eight 'Best 3rd X/Y/..'
    R32 slots via a backtracking perfect matching that always respects each
    slot's allowed-group list (greedy could dead-end and leave a slot empty)."""
    best = _best_thirds(conn)
    if best is None:
        return {}
    by_group = {t["group"]: t["team"] for t in best}
    qualified_groups = set(by_group)

    slots = []  # (match_id, side, allowed_groups_that_qualified)
    for s in conn.execute(
            "SELECT id,home_ref,away_ref FROM matches WHERE round='R32' ORDER BY match_no"):
        for side, ref in (("home", s["home_ref"]), ("away", s["away_ref"])):
            mt = re.match(r"Best 3rd ([A-L/]+)", ref or "")
            if mt:
                allowed = set(mt.group(1).split("/")) & qualified_groups
                slots.append([s["id"], side, allowed])

    # most-constrained slot first for fast, reliable matching
    order = sorted(range(len(slots)), key=lambda i: len(slots[i][2]))
    assignment = {}
    used = set()

    def bt(k):
        if k == len(order):
            return True
        sid, side, allowed = slots[order[k]]
        for g in sorted(allowed, key=lambda g: -best.index(next(t for t in best if t["group"] == g))):
            if g in used:
                continue
            used.add(g)
            assignment[(sid, side)] = by_group[g]
            if bt(k + 1):
                return True
            used.discard(g)
            assignment.pop((sid, side), None)
        return False

    bt(0)
    return assignment


def _resolve_ref(conn, ref, thirds_assignment, match_id, side):
    """Turn a placeholder into a concrete team name, or None if unknown yet."""
    if ref is None:
        return None
    m = re.match(r"Winner ([A-L])$", ref)
    if m:
        g = m.group(1)
        if group_complete(conn, g):
            return group_table(conn, g)[0]["team"]
        return None
    m = re.match(r"Runner-up ([A-L])$", ref)
    if m:
        g = m.group(1)
        if group_complete(conn, g):
            return group_table(conn, g)[1]["team"]
        return None
    if ref.startswith("Best 3rd"):
        return thirds_assignment.get((match_id, side))
    m = re.match(r"Winner M(\d+)$", ref)
    if m:
        return ko_winner(conn, f"M{m.group(1)}")
    m = re.match(r"Loser M(\d+)$", ref)
    if m:
        return ko_loser(conn, f"M{m.group(1)}")
    # already a literal team name (group stage)
    if conn.execute("SELECT 1 FROM teams WHERE name=?", (ref,)).fetchone():
        return ref
    return None


def resolve(conn):
    """Recompute all resolvable team slots across the knockout bracket."""
    thirds = _assign_thirds(conn)
    # iterate a few times so winners cascade R32 -> R16 -> QF -> SF -> FINAL
    for _ in range(6):
        changed = False
        kos = conn.execute("SELECT * FROM matches WHERE stage='ko' ORDER BY match_no")
        for m in kos:
            ht = _resolve_ref(conn, m["home_ref"], thirds, m["id"], "home")
            at = _resolve_ref(conn, m["away_ref"], thirds, m["id"], "away")
            if ht != m["home_team"] or at != m["away_team"]:
                conn.execute("UPDATE matches SET home_team=?, away_team=? WHERE id=?",
                             (ht, at, m["id"]))
                changed = True
        if not changed:
            break
    conn.commit()
