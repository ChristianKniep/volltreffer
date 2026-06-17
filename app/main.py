"""FastAPI application: serves the static site and the JSON API.

Multi-user: every data route is scoped to the logged-in user. Betting backends
are plugins (see app/providers/); each user stores their own credentials,
encrypted, and tips are kept per user + provider.
"""
import datetime
import os
import pathlib
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, providers, resolve as R, score as SC, updater as U
from .predict import predict, rationale, HOST_BONUS, BASE_XG, ELO_DIV

STATIC = pathlib.Path(__file__).resolve().parent / "static"

GROUP_COLOR = {
    'A':'#C8102E','B':'#CC6B1F','C':'#A9821B','D':'#5E7A1E','E':'#2E8B57','F':'#0E7C7B',
    'G':'#1F6FB2','H':'#2C3E8C','I':'#6A359C','J':'#A61E4D','K':'#8B4A2B','L':'#3D5A6C',
}
HEAT = {5:'#B3122B',4:'#E0561F',3:'#E59020',2:'#C7A63C',1:'#9B9082'}
ROUND_BASE = {'R32':3.3,'R16':3.8,'QF':4.3,'SF':4.7,'FINAL':5.3,'3RD':3.2}
RIVALRY = {
    frozenset(('England','Croatia')), frozenset(('Brazil','Morocco')),
    frozenset(('Spain','Uruguay')), frozenset(('Portugal','Colombia')),
    frozenset(('France','Senegal')),
}
RMIN, RMAX = 1330, 1877
ALLOW_REG = os.environ.get("ALLOW_REGISTRATION", "true").lower() in ("1", "true", "yes")
DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "Europe/Berlin")
# Host city -> IANA timezone, used to show kickoff in the venue's local time.
VENUE_TZ = {
    "Mexico City": "America/Mexico_City",
    "Zapopan": "America/Mexico_City",      # Guadalajara metro, Central
    "Monterrey": "America/Monterrey",
    "Atlanta": "America/New_York",
    "Toronto": "America/Toronto",
    "Santa Clara": "America/Los_Angeles",
    "Inglewood": "America/Los_Angeles",    # Los Angeles
    "Vancouver": "America/Vancouver",
    "Seattle": "America/Los_Angeles",
    "East Rutherford": "America/New_York", # NY/NJ
    "Foxborough": "America/New_York",      # Boston
    "Philadelphia": "America/New_York",
    "Miami Gardens": "America/New_York",
    "Houston": "America/Chicago",
    "Kansas City": "America/Chicago",
    "Arlington": "America/Chicago",        # Dallas
}
# how good each local hour (0–23) is by default, 1 (avoid) … 5 (perfect); users customise this.
DEFAULT_SLOTS = [3, 2, 2, 1, 1, 1, 2, 2, 3, 3, 3, 3,
                 3, 3, 3, 3, 4, 4, 5, 5, 5, 5, 5, 4]

# --- German free-to-air (öffentlich-rechtlich) broadcaster per match ----------
# ARD/ZDF have historically shared the World Cup: they alternate which channel
# carries the day's games by calendar matchday, and BOTH air every Germany game.
# This is a deterministic derivation (no per-match list was published yet); it is
# isolated here and easy to correct once the official ARD/ZDF split is announced.
# Override individual matches via the BROADCAST_OVERRIDE map (match id -> label).
GERMANY = "Germany"
TOURNAMENT_START = datetime.date(2026, 6, 11)  # opener; day 0 of the rota
BROADCAST_OVERRIDE: dict[str, str] = {}


def _broadcaster(match_id, kickoff_et, home, away):
    """Return the German public-TV station(s) showing this match.
    Both ARD & ZDF always carry Germany's games; otherwise the day alternates."""
    if match_id in BROADCAST_OVERRIDE:
        return BROADCAST_OVERRIDE[match_id]
    if home == GERMANY or away == GERMANY:
        return "ARD/ZDF"
    day = datetime.datetime.strptime(kickoff_et, "%Y-%m-%d %H:%M").date()
    even = (day - TOURNAMENT_START).days % 2 == 0
    return "ARD" if even else "ZDF"

app = FastAPI(title="World Cup 2026")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.on_event("startup")
def _startup():
    db.init_and_seed()
    conn = db.connect()
    R.resolve(conn)
    conn.close()


# ---------- excitement / time helpers (unchanged) ----------
def _tier(score):
    if score >= 4.5: return 5
    if score >= 4.0: return 4
    if score >= 3.4: return 3
    if score >= 2.7: return 2
    return 1


def _team_quality_score(ra, rb):
    avg = (ra + rb) / 2.0
    q = max(0.0, min(1.0, (avg - RMIN) / (RMAX - RMIN)))
    score = 2.0 + q * 2.6
    d = abs(ra - rb)
    if d <= 40: score += 0.5
    elif d <= 90: score += 0.3
    elif d <= 150: score += 0.15
    if max(ra, rb) >= 1820: score += 0.3
    elif max(ra, rb) >= 1760: score += 0.15
    return score


def _excitement(m, ratings):
    rnd = m["round"]
    h, a = m["home_team"], m["away_team"]
    if m["stage"] == "ko":
        base = ROUND_BASE.get(rnd, 3.3)
        score = max(base, _team_quality_score(ratings[h], ratings[a])) if h and a else base
    else:
        score = _team_quality_score(ratings[h], ratings[a])
        if frozenset((h, a)) in RIVALRY:
            score += 0.45
    t = _tier(score)
    return {"tier": t, "color": HEAT[t]}


def _slot_color(rating):
    """Map a 1–5 slot rating to the green(good)→red(bad) kick-off tint."""
    s = (max(1, min(5, int(rating))) - 1) / 4.0
    return f"hsl({int(round(s*125))},72%,{int(round(81-(1-s)*5))}%)"


def _user_tz(tz_name):
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ), (tz_name or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 - unknown/missing zone falls back
        return ZoneInfo(DEFAULT_TZ), DEFAULT_TZ


# ---------- providers ----------
def _provider_labels():
    return {p.id: p.label for p in providers.all_providers()}


def _active_provider(conn, user_id, requested=None):
    """Pick which provider's tips to show: the requested one if configured,
    else the user's first configured provider, else any provider that has tips
    (e.g. migrated legacy tips before the backend is reconnected), else None."""
    configured = db.configured_provider_ids(conn, user_id)
    if requested and requested in configured:
        return requested
    if configured:
        return configured[0]
    row = conn.execute(
        "SELECT provider_id FROM tips WHERE user_id=? ORDER BY provider_id LIMIT 1",
        (user_id,)).fetchone()
    return row["provider_id"] if row else None


# ---------- leaderboard / pool helpers ----------
def _kickoff_utc(kickoff_et):
    """Fixtures store US-Eastern wall-clock (EDT = UTC-4) for the whole window."""
    et = datetime.datetime.strptime(kickoff_et, "%Y-%m-%d %H:%M")
    return et + datetime.timedelta(hours=4)


def _approved_usernames(conn):
    return {r["id"]: r["username"]
            for r in conn.execute("SELECT id,username FROM users WHERE approved=1")}


def _ghost_name_set(conn):
    """Lowercased display-names of imported teamtip members. Used to drop the
    duplicate app-user row when the same person is also a teamtip ghost (the
    ghost carries teamtip's authoritative points, so we keep the ghost)."""
    return {(m["display_name"] or "").strip().lower() for m in db.get_members(conn)}


def _user_match_tips(conn):
    """{user_id: {match_id: (home, away)}}, one tip per user+match (latest wins
    if the user has tips from several providers)."""
    latest = {}   # (uid, mid) -> (updated_at, home, away)
    for r in conn.execute("SELECT user_id,match_id,home,away,updated_at FROM tips"):
        key = (r["user_id"], r["match_id"])
        ts = r["updated_at"] or ""
        if key not in latest or ts >= latest[key][0]:
            latest[key] = (ts, r["home"], r["away"])
    out = {}
    for (uid, mid), (_, h, a) in latest.items():
        out.setdefault(uid, {})[mid] = (h, a)
    return out


def _finished_results(conn):
    return {m["id"]: (m["home_score"], m["away_score"])
            for m in conn.execute(
                "SELECT id,home_score,away_score FROM matches WHERE status='finished'")}


def _score_tips(tips_for_subject, results):
    """Aggregate one subject's tips into a points/outcome breakdown."""
    agg = {"points": 0, "exact": 0, "goaldiff": 0, "tendency": 0, "miss": 0, "tips": 0}
    for mid, (th, ta) in tips_for_subject.items():
        if mid not in results:
            continue
        cls = SC.classify(th, ta, *results[mid])
        agg[cls] += 1
        agg["points"] += SC.POINTS[cls]
        agg["tips"] += 1
    return agg


def _rank_rows(rows):
    """Sort by points then tie-breakers and assign competition ranks (ties share
    a rank). Mutates and returns rows."""
    rows.sort(key=lambda r: (-r["points"], -r["exact"], -r["goaldiff"],
                             -r["tendency"], r["username"].lower()))
    rank = 0
    prev = None
    for i, r in enumerate(rows, 1):
        keyv = (r["points"], r["exact"], r["goaldiff"], r["tendency"])
        if keyv != prev:
            rank = i
            prev = keyv
        r["rank"] = rank
    return rows


# account-view modes for leaderboard / matchday:
#   "ghosts" -> only imported teamtip members (default; the real pool)
#   "local"  -> only native app users
#   "both"   -> everyone, but a name that exists as both is shown once (ghost kept)
ACCOUNT_VIEWS = ("ghosts", "local", "both")


def _leaderboard(conn, me_id, view="both"):
    if view not in ACCOUNT_VIEWS:
        view = "both"
    results = _finished_results(conn)
    user_tips = _user_match_tips(conn)
    ghost_names = _ghost_name_set(conn)
    rows = []
    # real app users
    if view in ("local", "both"):
        for uid, username in _approved_usernames(conn).items():
            # in 'both', drop a local row that duplicates a ghost (keep the ghost,
            # which carries teamtip's authoritative points)
            if view == "both" and (username or "").strip().lower() in ghost_names:
                continue
            agg = _score_tips(user_tips.get(uid, {}), results)
            rows.append({"kind": "user", "user_id": uid, "username": username,
                         "is_self": uid == me_id, **agg})
    # teamtip ghost members (read-only, imported from the betgame).
    # Prefer teamtip's OWN ranking numbers (source of truth) so our leaderboard
    # matches teamtip exactly; fall back to recomputing if they weren't synced.
    if view in ("ghosts", "both"):
        member_tips = db.get_member_tips(conn)
        for m in db.get_members(conn):
            key = (m["betgame_id"], m["fk_user"])
            agg = _score_tips(member_tips.get(key, {}), results)
            if m.get("tt_points") is not None:
                agg = {**agg,
                       "points": m["tt_points"],
                       "exact": m["tt_exact"] if m.get("tt_exact") is not None else agg["exact"],
                       "goaldiff": m["tt_goaldiff"] if m.get("tt_goaldiff") is not None else agg["goaldiff"],
                       "tendency": m["tt_tendency"] if m.get("tt_tendency") is not None else agg["tendency"],
                       "tips": m["tt_betcount"] if m.get("tt_betcount") is not None else agg["tips"]}
                source = "teamtip"
            else:
                source = "computed"
            rows.append({"kind": "teamtip",
                         "user_id": f"{m['betgame_id']}:{m['fk_user']}",
                         "username": m["display_name"], "is_self": False,
                         "points_source": source, **agg})
    return _rank_rows(rows)


def _snapshot_standings(conn):
    """Persist the current standings as a snapshot tagged by matchday (= number
    of finished matches). Idempotent per matchday. Source of truth for graphs."""
    matchday = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE status='finished'").fetchone()["c"]
    if matchday == 0:
        return 0  # nothing scored yet — no meaningful snapshot
    rows = _leaderboard(conn, me_id=None)
    db.write_snapshot(conn, matchday, [{
        "subject_kind": r["kind"], "subject_id": r["user_id"],
        "display_name": r["username"], "points": r["points"],
        "exact": r["exact"], "goaldiff": r["goaldiff"], "tendency": r["tendency"],
        "betcount": r["tips"], "rank": r["rank"],
    } for r in rows])
    return matchday


# ---------- state ----------
def _state(user_id, provider_id):
    conn = db.connect()
    teams = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    ratings = {n: t["rating"] for n, t in teams.items()}
    hosts = {n: bool(t["host"]) for n, t in teams.items()}

    tips = {}
    if provider_id:
        for r in conn.execute(
                "SELECT match_id,home,away,updated_at FROM tips WHERE user_id=? AND provider_id=?",
                (user_id, provider_id)):
            tips[r["match_id"]] = {"home": r["home"], "away": r["away"],
                                   "updated_at": r["updated_at"]}
    overrides = db.get_overrides(conn)
    user_overrides = db.get_user_overrides(conn, user_id)

    tz_pref, slots_pref = db.get_prefs(conn, user_id)
    tz, tz_name = _user_tz(tz_pref)
    slots = slots_pref if (isinstance(slots_pref, list) and len(slots_pref) == 24) else DEFAULT_SLOTS

    groups = {}
    for g in R.GROUPS:
        groups[g] = {
            "color": GROUP_COLOR[g],
            "complete": R.group_complete(conn, g),
            "standings": [
                {**{k: row[k] for k in ("rank","team","P","W","D","L","GF","GA","GD","Pts")},
                 "iso": teams[row["team"]]["iso"]}
                for row in R.group_table(conn, g)
            ],
        }

    matches = []
    for m in conn.execute("SELECT * FROM matches ORDER BY kickoff_et, match_no"):
        et = datetime.datetime.strptime(m["kickoff_et"], "%Y-%m-%d %H:%M")
        # kickoff_et is US-Eastern wall-clock (EDT, UTC-4) → UTC instant → user's tz
        utc = et.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=4)
        local = utc.astimezone(tz)
        venue_zone = VENUE_TZ.get(m["city"])
        venue = utc.astimezone(ZoneInfo(venue_zone)) if venue_zone else None
        rating = slots[local.hour]
        h, a = m["home_team"], m["away_team"]
        pred = None
        if h and a:
            p = predict(ratings[h], ratings[a], hosts.get(h, False), hosts.get(a, False),
                        knockout=(m["stage"] == "ko"))
            p["rationale"] = rationale(h, a, p, knockout=(m["stage"] == "ko"))
            ov = overrides.get(m["id"])
            if ov:
                p.update(ov)            # global (admin/automation) override
                p["overridden"] = True
                p["override_source"] = "shared"
            uov = user_overrides.get(m["id"])
            if uov:
                p.update(uov)           # this user's own view wins last
                p["overridden"] = True
                p["override_source"] = "you"
            pred = p
        tip = tips.get(m["id"])
        if tip and pred:
            tip = {**tip, "differs": (tip["home"], tip["away"]) != (p["score_home"], p["score_away"])}
        matches.append({
            "id": m["id"], "stage": m["stage"], "round": m["round"], "group": m["grp"],
            "match_no": m["match_no"], "venue": m["venue"], "city": m["city"],
            "kickoff_et": m["kickoff_et"],
            "local_date": local.strftime("%a %d %b").upper(),
            "local_time": local.strftime("%H:%M"), "et_time": et.strftime("%H:%M"),
            "tz_abbr": local.strftime("%Z"),
            "venue_time": venue.strftime("%H:%M") if venue else et.strftime("%H:%M"),
            "venue_tz_abbr": venue.strftime("%Z") if venue else "ET",
            "broadcaster": _broadcaster(m["id"], m["kickoff_et"], h, a),
            "home_ref": m["home_ref"], "away_ref": m["away_ref"],
            "home": h, "away": a,
            "home_iso": teams[h]["iso"] if h else None,
            "away_iso": teams[a]["iso"] if a else None,
            "home_score": m["home_score"], "away_score": m["away_score"],
            "home_pens": m["home_pens"], "away_pens": m["away_pens"],
            "winner": R.ko_winner(conn, m["id"]) if m["stage"] == "ko" else None,
            "status": m["status"],
            "excitement": _excitement(m, ratings),
            "time_color": _slot_color(rating),
            "time_rating": rating,
            "prediction": pred,
            "tip": tip,
        })

    meta = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta")}
    finished = sum(1 for x in matches if x["status"] == "finished")
    labels = _provider_labels()
    configured = [{"id": pid, "label": labels.get(pid, pid)}
                  for pid in db.configured_provider_ids(conn, user_id)]
    conn.close()
    return {"meta": {**meta, "total": len(matches), "finished": finished,
                     "active_provider": provider_id, "providers": configured,
                     "timezone": tz_name},
            "groups": groups, "matches": matches}


# ---------- routes: pages ----------
@app.get("/")
def index():
    # never cache the HTML shell, so a new ?v= for app.js/style.css is always
    # picked up immediately (the versioned static assets themselves can cache).
    return FileResponse(str(STATIC / "index.html"),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# ---------- routes: auth ----------
class Credentials(BaseModel):
    username: str
    password: str


def _user_payload(user):
    role = user["role"]
    return {"authenticated": True, "username": user["username"], "role": role,
            "is_admin": role == "admin", "approved": bool(user["approved"]),
            "registration_open": ALLOW_REG}


@app.get("/api/auth/me")
def auth_me(user=Depends(auth.optional_user)):
    if not user:
        return {"authenticated": False, "registration_open": ALLOW_REG}
    return _user_payload(user)


@app.post("/api/auth/register")
def auth_register(c: Credentials, response: Response):
    if not ALLOW_REG:
        raise HTTPException(403, "registration is disabled")
    if len(c.username.strip()) < 2 or len(c.password) < 6:
        raise HTTPException(400, "username min 2 chars, password min 6 chars")
    conn = db.connect()
    try:
        if auth.get_user_by_name(conn, c.username):
            raise HTTPException(409, "username already taken")
        uid = auth.create_user(conn, c.username, c.password)
        token = auth.start_session(conn, uid)
        user = auth.get_user_by_name(conn, c.username)
    finally:
        conn.close()
    response.set_cookie(value=token, **auth.cookie_kwargs())
    return _user_payload(user)


@app.post("/api/auth/login")
def auth_login(c: Credentials, response: Response):
    conn = db.connect()
    try:
        user = auth.get_user_by_name(conn, c.username)
        if not user or not auth.verify_password(c.password, user["pw_hash"]):
            raise HTTPException(401, "invalid username or password")
        token = auth.start_session(conn, user["id"])
    finally:
        conn.close()
    response.set_cookie(value=token, **auth.cookie_kwargs())
    return _user_payload(user)


@app.post("/api/auth/logout")
def auth_logout(response: Response, wc_session: str | None = Cookie(default=None)):
    if wc_session:
        conn = db.connect()
        try:
            auth.end_session(conn, wc_session)
        finally:
            conn.close()
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# ---- personal API token (for the prediction skill) ----
@app.get("/api/auth/token")
def auth_token_get(user=Depends(auth.current_user)):
    return {"token": user["api_token"]}


@app.post("/api/auth/token")
def auth_token_rotate(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        token = auth.rotate_api_token(conn, user["id"])
    finally:
        conn.close()
    return {"token": token}


@app.delete("/api/auth/token")
def auth_token_revoke(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        auth.clear_api_token(conn, user["id"])
    finally:
        conn.close()
    return {"ok": True}


# ---- display prefs: timezone + kick-off slot ratings ----
@app.get("/api/me/prefs")
def me_prefs_get(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        tz, slots = db.get_prefs(conn, user["id"])
    finally:
        conn.close()
    return {"timezone": tz or DEFAULT_TZ, "tz_explicit": tz is not None,
            "slots": slots if (isinstance(slots, list) and len(slots) == 24) else DEFAULT_SLOTS,
            "default_slots": DEFAULT_SLOTS}


class PrefsBody(BaseModel):
    timezone: str | None = None
    slots: list[int] | None = None


@app.put("/api/me/prefs")
def me_prefs_put(body: PrefsBody, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        if body.timezone is not None:
            try:
                ZoneInfo(body.timezone)
            except Exception:  # noqa: BLE001
                raise HTTPException(400, f"unknown timezone: {body.timezone}")
            db.set_timezone(conn, user["id"], body.timezone)
        if body.slots is not None:
            if len(body.slots) != 24 or any(x < 1 or x > 5 for x in body.slots):
                raise HTTPException(400, "slots must be 24 integers in 1..5")
            db.set_slot_ratings(conn, user["id"], [int(x) for x in body.slots])
    finally:
        conn.close()
    return {"ok": True}


# ---------- routes: admin (user management) ----------
@app.get("/api/admin/users")
def admin_users(admin=Depends(auth.require_admin)):
    conn = db.connect()
    try:
        rows = auth.list_users(conn)
    finally:
        conn.close()
    return {"users": [{"id": r["id"], "username": r["username"], "role": r["role"],
                       "approved": bool(r["approved"]), "created_at": r["created_at"],
                       "is_self": r["id"] == admin["id"]} for r in rows]}


@app.post("/api/admin/users/{uid}/approve")
def admin_approve(uid: int, admin=Depends(auth.require_admin)):
    conn = db.connect()
    try:
        auth.set_user_approved(conn, uid, 1)
    finally:
        conn.close()
    return {"ok": True}


class RoleBody(BaseModel):
    role: str


@app.post("/api/admin/users/{uid}/role")
def admin_set_role(uid: int, body: RoleBody, admin=Depends(auth.require_admin)):
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "role must be 'admin' or 'user'")
    conn = db.connect()
    try:
        target = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise HTTPException(404, "user not found")
        if target["role"] == "admin" and body.role != "admin" and auth.admin_count(conn) <= 1:
            raise HTTPException(400, "can't demote the last admin")
        auth.set_user_role(conn, uid, body.role)
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
def admin_delete(uid: int, admin=Depends(auth.require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "can't remove your own account")
    conn = db.connect()
    try:
        target = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise HTTPException(404, "user not found")
        if target["role"] == "admin" and auth.admin_count(conn) <= 1:
            raise HTTPException(400, "can't remove the last admin")
        auth.delete_user(conn, uid)
    finally:
        conn.close()
    return {"ok": True}


# ---------- routes: prediction model (agent-writable) ----------
@app.get("/api/ratings")
def api_ratings_get(principal=Depends(auth.automation_or_admin)):
    conn = db.connect()
    try:
        return {"ratings": db.get_ratings(conn),
                "model": {"host_bonus": HOST_BONUS, "base_xg": BASE_XG, "elo_div": ELO_DIV},
                "overrides": db.get_overrides(conn)}
    finally:
        conn.close()


class RatingsBody(BaseModel):
    ratings: dict[str, int]


@app.put("/api/ratings")
def api_ratings_put(body: RatingsBody, principal=Depends(auth.automation_or_admin)):
    if not body.ratings:
        raise HTTPException(400, "no ratings supplied")
    conn = db.connect()
    try:
        updated, unknown = db.update_ratings(conn, body.ratings)
    finally:
        conn.close()
    return {"ok": True, "updated": updated, "unknown": unknown}


# accepted override fields (subset of a prediction); everything else is ignored
_OVERRIDE_FIELDS = {"score_home", "score_away", "p_home", "p_draw", "p_away",
                    "adv_home", "adv_away", "favored", "confidence", "rationale"}


@app.post("/api/match/{mid}/prediction")
def api_prediction_set(mid: str, body: dict, principal=Depends(auth.automation_or_admin)):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM matches WHERE id=?", (mid,)).fetchone():
            raise HTTPException(404, f"match {mid} not found")
        data = {k: body[k] for k in _OVERRIDE_FIELDS if k in body}
        if not data:
            raise HTTPException(400, f"supply at least one of: {sorted(_OVERRIDE_FIELDS)}")
        db.set_override(conn, mid, data)
    finally:
        conn.close()
    return {"ok": True, "match_id": mid, "override": data}


@app.delete("/api/match/{mid}/prediction")
def api_prediction_clear(mid: str, principal=Depends(auth.automation_or_admin)):
    conn = db.connect()
    try:
        db.delete_override(conn, mid)
    finally:
        conn.close()
    return {"ok": True}


# ---- per-user prediction overrides (the caller's own view) ----
@app.post("/api/match/{mid}/my-prediction")
def api_my_prediction_set(mid: str, body: dict, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM matches WHERE id=?", (mid,)).fetchone():
            raise HTTPException(404, f"match {mid} not found")
        data = {k: body[k] for k in _OVERRIDE_FIELDS if k in body}
        if not data:
            raise HTTPException(400, f"supply at least one of: {sorted(_OVERRIDE_FIELDS)}")
        db.set_user_override(conn, user["id"], mid, data)
    finally:
        conn.close()
    return {"ok": True, "match_id": mid, "prediction": data}


@app.delete("/api/match/{mid}/my-prediction")
def api_my_prediction_clear(mid: str, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        db.delete_user_override(conn, user["id"], mid)
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/my-predictions")
def api_my_predictions(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        return {"overrides": db.get_user_overrides(conn, user["id"])}
    finally:
        conn.close()


# ---------- routes: providers ----------
def _provider_view(conn, user_id, cls):
    saved = db.get_provider_creds(conn, user_id, cls.id)
    # echo back only non-secret field values; secrets become a "set/unset" flag
    summary = {}
    for f in cls.credential_fields:
        if f.secret:
            summary[f.name] = "********" if saved.get(f.name) else ""
        else:
            summary[f.name] = saved.get(f.name, "")
    view = {**cls.describe(), "configured": bool(saved), "values": summary}
    # surface JWT expiry so the UI can warn before a teamtip token lapses
    if cls.id == "teamtip" and saved.get("token"):
        from .providers.teamtip import token_exp
        exp = token_exp(saved.get("token"))
        if exp:
            view["token_exp"] = exp
            view["token_expired"] = exp <= datetime.datetime.utcnow().timestamp()
    return view


@app.get("/api/providers")
def providers_list(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        return {"providers": [_provider_view(conn, user["id"], p.__class__)
                              for p in (cls() for cls in providers.all_providers())]}
    finally:
        conn.close()


@app.put("/api/providers/{pid}/credentials")
def providers_save(pid: str, body: dict, user=Depends(auth.current_user)):
    prov = providers.get_provider(pid)
    if not prov:
        raise HTTPException(404, f"unknown provider {pid}")
    conn = db.connect()
    try:
        existing = db.get_provider_creds(conn, user["id"], pid)
        merged = dict(existing)
        for f in prov.credential_fields:
            val = body.get(f.name)
            if f.secret and (val is None or val == "" or val == "********"):
                continue  # keep the stored secret untouched
            if val is not None:
                merged[f.name] = val
        ok, msg = prov.validate(merged)
        db.set_provider_creds(conn, user["id"], pid, merged)
        # auto-import the teamtip group on a successful connect so members show
        # up immediately, without needing the separate "Sync group" button.
        group = None
        if ok and pid == "teamtip" and hasattr(prov, "sync_group"):
            try:
                group = prov.sync_group(conn, user["id"], merged)
                _snapshot_standings(conn)
            except Exception as e:  # noqa: BLE001 - never fail the save on sync
                group = {"errors": [f"group import failed: {e}"]}
    finally:
        conn.close()
    return {"ok": True, "valid": ok, "message": msg, "group": group}


@app.post("/api/providers/{pid}/test")
def providers_test(pid: str, user=Depends(auth.current_user)):
    prov = providers.get_provider(pid)
    if not prov:
        raise HTTPException(404, f"unknown provider {pid}")
    conn = db.connect()
    try:
        creds = db.get_provider_creds(conn, user["id"], pid)
    finally:
        conn.close()
    if not creds:
        raise HTTPException(400, "no credentials saved for this provider")
    ok, msg = prov.validate(creds)
    return {"ok": ok, "message": msg}


@app.delete("/api/providers/{pid}/credentials")
def providers_delete(pid: str, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        db.delete_provider_creds(conn, user["id"], pid)
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/providers/{pid}/sync")
def providers_sync(pid: str, user=Depends(auth.current_user)):
    prov = providers.get_provider(pid)
    if not prov:
        raise HTTPException(404, f"unknown provider {pid}")
    conn = db.connect()
    try:
        creds = db.get_provider_creds(conn, user["id"], pid)
        out = prov.sync_tips(conn, user["id"], creds)
    finally:
        conn.close()
    return out


# ---------- routes: state ----------
@app.get("/api/state")
def api_state(provider: str | None = None, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        active = _active_provider(conn, user["id"], provider)
    finally:
        conn.close()
    return _state(user["id"], active)


# ---------- routes: leaderboard / pool ----------
@app.get("/api/leaderboard")
def api_leaderboard(view: str = "ghosts", user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        rows = _leaderboard(conn, user["id"], view=view)
        finished = conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE status='finished'").fetchone()["c"]
    finally:
        conn.close()
    return {"scheme": SC.scheme(), "scored_matches": finished,
            "view": view if view in ACCOUNT_VIEWS else "both", "standings": rows}


@app.get("/api/match/{mid}/tips")
def api_match_tips(mid: str, user=Depends(auth.current_user)):
    """All players' tips for one game. Revealed only once kickoff has passed, so
    nobody can peek at others' predictions beforehand."""
    conn = db.connect()
    try:
        m = conn.execute(
            "SELECT id,kickoff_et,status,home_score,away_score FROM matches WHERE id=?",
            (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "match not found")
        finished = m["status"] == "finished"
        revealed = finished or datetime.datetime.utcnow() >= _kickoff_utc(m["kickoff_et"])
        if not revealed:
            return {"revealed": False, "finished": False, "tips": []}
        names = _approved_usernames(conn)
        tips = _user_match_tips(conn)
        res = (m["home_score"], m["away_score"]) if finished else None
        out = []
        for uid, username in names.items():
            t = tips.get(uid, {}).get(mid)
            if not t:
                continue
            entry = {"username": username, "home": t[0], "away": t[1],
                     "is_self": uid == user["id"]}
            if finished:
                cls = SC.classify(t[0], t[1], *res)
                entry["points"] = SC.POINTS[cls]
                entry["outcome"] = cls
            out.append(entry)
    finally:
        conn.close()
    if finished:
        out.sort(key=lambda e: (-e["points"], e["username"].lower()))
    else:
        out.sort(key=lambda e: e["username"].lower())
    return {"revealed": True, "finished": finished, "tips": out, "scheme": SC.scheme()}


# ---------- routes: results & tips ----------
class Result(BaseModel):
    home_score: int
    away_score: int
    home_pens: int | None = None
    away_pens: int | None = None


@app.post("/api/match/{mid}/result")
def api_result(mid: str, r: Result, user=Depends(auth.current_user)):
    conn = db.connect()
    ok = U.set_result(conn, mid, r.home_score, r.away_score, r.home_pens, r.away_pens)
    conn.close()
    if not ok:
        raise HTTPException(404, f"match {mid} not found")
    return {"ok": True}


class Tip(BaseModel):
    home: int
    away: int
    provider: str | None = None


@app.post("/api/match/{mid}/tip")
def api_tip_submit(mid: str, t: Tip, user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        pid = t.provider or _active_provider(conn, user["id"])
        if not pid:
            raise HTTPException(400, "no betting provider configured — add one in Settings")
        prov = providers.get_provider(pid)
        if not prov:
            raise HTTPException(404, f"unknown provider {pid}")
        creds = db.get_provider_creds(conn, user["id"], pid)
        out = prov.submit_tip(conn, user["id"], creds, mid, t.home, t.away)
    finally:
        conn.close()
    if not out.get("ok"):
        raise HTTPException(400, out.get("error", "tip submit failed"))
    return out


# back-compat alias for the old teamtip-specific endpoint
@app.post("/api/match/{mid}/teamtip")
def api_teamtip_submit(mid: str, t: Tip, user=Depends(auth.current_user)):
    t.provider = "teamtip"
    return api_tip_submit(mid, t, user)


@app.post("/api/update")
def api_update(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        summary = U.fetch_remote(conn)
        synced = {}
        for pid in db.configured_provider_ids(conn, user["id"]):
            prov = providers.get_provider(pid)
            creds = db.get_provider_creds(conn, user["id"], pid)
            synced[pid] = prov.sync_tips(conn, user["id"], creds)
            # pull the whole teamtip betgame too, so ghost members stay current
            if pid == "teamtip" and hasattr(prov, "sync_group"):
                synced["teamtip_group"] = prov.sync_group(conn, user["id"], creds)
        summary["providers"] = synced
        summary["snapshot_matchday"] = _snapshot_standings(conn)
    finally:
        conn.close()
    return summary


@app.post("/api/teamtip/sync")
def api_teamtip_sync(user=Depends(auth.current_user)):
    return providers_sync("teamtip", user)


@app.post("/api/teamtip/sync-group")
def api_teamtip_sync_group(user=Depends(auth.current_user)):
    """Import every betgame member (names + scorelines) as read-only ghosts,
    then snapshot the standings for the progress graphs."""
    prov = providers.get_provider("teamtip")
    if not prov or not hasattr(prov, "sync_group"):
        raise HTTPException(400, "teamtip group sync unavailable")
    conn = db.connect()
    try:
        creds = db.get_provider_creds(conn, user["id"], "teamtip")
        if not creds:
            raise HTTPException(400, "no teamtip credentials saved — add them in Settings")
        out = prov.sync_group(conn, user["id"], creds)
        out["snapshot_matchday"] = _snapshot_standings(conn)
    finally:
        conn.close()
    return out


def _progress_subjects(conn, view):
    """All subjects (app users + teamtip ghosts) honoring the account view, as
    {subject_id: {"name","kind","tips":{match_id:(h,a)}}}."""
    out = {}
    ghost_names = _ghost_name_set(conn)
    if view in ("local", "both"):
        user_tips = _user_match_tips(conn)
        for uid, uname in _approved_usernames(conn).items():
            if view == "both" and (uname or "").strip().lower() in ghost_names:
                continue
            out[f"u{uid}"] = {"name": uname, "kind": "user",
                              "tips": user_tips.get(uid, {})}
    if view in ("ghosts", "both"):
        member_tips = db.get_member_tips(conn)
        for m in db.get_members(conn):
            sid = f"{m['betgame_id']}:{m['fk_user']}"
            out[sid] = {"name": m["display_name"], "kind": "teamtip",
                        "tips": member_tips.get((m["betgame_id"], m["fk_user"]), {})}
    return out


def _progress_steps(conn, granularity):
    """Ordered list of (step_key, step_label, [match_ids]) over FINISHED matches,
    grouped by the chosen granularity. 'match' = one per game, 'day' = per kickoff
    calendar date, 'round' = per group round / KO round. Chronological by kickoff."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id,kickoff_et,home_team,away_team,home_ref,away_ref FROM matches "
        "WHERE status='finished' ORDER BY kickoff_et, match_no")]
    if not rows:
        return []
    if granularity == "match":
        steps = []
        for m in rows:
            h = m["home_team"] or m["home_ref"]
            a = m["away_team"] or m["away_ref"]
            steps.append((m["id"], f"{h}–{a}", [m["id"]]))
        return steps
    if granularity == "day":
        buckets = {}
        for m in rows:
            buckets.setdefault(m["kickoff_et"][:10], []).append(m["id"])
        return [(d, d, ids) for d, ids in sorted(buckets.items())]
    # round
    keys = _matchday_keys(conn)
    order = {}
    buckets = {}
    for m in rows:
        k, label, o = keys[m["id"]]
        order[k] = (o, label)
        buckets.setdefault(k, []).append(m["id"])
    return [(k, order[k][1], buckets[k]) for k in sorted(buckets, key=lambda k: order[k][0])]


@app.get("/api/progress")
def api_progress(view: str = "ghosts", granularity: str = "round",
                 user=Depends(auth.current_user)):
    """Cumulative standings over time, computed deterministically from tips +
    results (no reliance on click-time snapshots). `granularity` ∈ match|day|round,
    `view` ∈ ghosts|local|both. For each step returns every subject's cumulative
    points & rank plus the per-step gains broken into exact/goaldiff/tendency."""
    if view not in ACCOUNT_VIEWS:
        view = "both"
    if granularity not in ("match", "day", "round"):
        granularity = "round"
    conn = db.connect()
    try:
        subjects = _progress_subjects(conn, view)
        results = _finished_results(conn)
        steps = _progress_steps(conn, granularity)
    finally:
        conn.close()

    # running cumulative state per subject
    cum = {sid: {"points": 0, "exact": 0, "goaldiff": 0, "tendency": 0}
           for sid in subjects}
    series = {sid: {"subject_id": sid, "name": s["name"], "kind": s["kind"],
                    "points": [], "rank": [], "gains": []}
              for sid, s in subjects.items()}
    step_meta = []

    for skey, slabel, mids in steps:
        # per-step gains for each subject
        for sid, s in subjects.items():
            g = {"points": 0, "exact": 0, "goaldiff": 0, "tendency": 0}
            for mid in mids:
                if mid not in results:
                    continue
                t = s["tips"].get(mid)
                if not t:
                    continue
                cls = SC.classify(t[0], t[1], *results[mid])
                if cls == "miss":
                    continue
                g[cls] += 1
                g["points"] += SC.POINTS[cls]
            c = cum[sid]
            c["points"] += g["points"]; c["exact"] += g["exact"]
            c["goaldiff"] += g["goaldiff"]; c["tendency"] += g["tendency"]
            series[sid]["gains"].append(g)
            series[sid]["points"].append(c["points"])
        # rank at this step (standard competition ranking, ties share a rank)
        ordered = sorted(subjects, key=lambda sid: (
            -cum[sid]["points"], -cum[sid]["exact"], -cum[sid]["goaldiff"],
            -cum[sid]["tendency"], series[sid]["name"].lower()))
        rank = 0; prev = None
        for i, sid in enumerate(ordered, 1):
            c = cum[sid]
            keyv = (c["points"], c["exact"], c["goaldiff"], c["tendency"])
            if keyv != prev:
                rank = i; prev = keyv
            series[sid]["rank"].append(rank)
        step_meta.append({"key": skey, "label": slabel, "matches": len(mids)})

    return {"granularity": granularity, "view": view,
            "steps": step_meta, "series": list(series.values()),
            "scheme": SC.scheme()}


# Knockout rounds in tournament order, with display labels.
_KO_ORDER = ["R32", "R16", "QF", "SF", "3RD", "FINAL"]
_KO_LABEL = {"R32": "Round of 32", "R16": "Round of 16", "QF": "Quarter-finals",
             "SF": "Semi-finals", "3RD": "Third place", "FINAL": "Final"}


def _matchday_keys(conn):
    """Map each match id -> (key, label, sort_order). A 'matchday' is now a round:
    group stage split into each team's 1st/2nd/3rd game (g1/g2/g3), then each KO
    round. Group round is derived from per-team kickoff order and is consistent
    for both teams in every fixture (verified against the schedule)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id,stage,grp,round,home_ref,away_ref,kickoff_et FROM matches")]
    # per-team appearance index within the group stage
    appear = {}
    seen = {}
    for m in sorted((x for x in rows if x["stage"] == "group"),
                    key=lambda x: x["kickoff_et"]):
        n = max(seen.get(m["home_ref"], 0), seen.get(m["away_ref"], 0)) + 1
        seen[m["home_ref"]] = n
        seen[m["away_ref"]] = n
        appear[m["id"]] = n
    out = {}
    for m in rows:
        if m["stage"] == "group":
            n = appear.get(m["id"], 1)
            out[m["id"]] = (f"g{n}", f"Group · Round {n}", n)
        else:
            rnd = m["round"]
            order = 10 + (_KO_ORDER.index(rnd) if rnd in _KO_ORDER else 99)
            out[m["id"]] = (rnd, _KO_LABEL.get(rnd, rnd), order)
    return out


@app.get("/api/matchdays")
def api_matchdays(user=Depends(auth.current_user)):
    """List of matchdays (group rounds 1/2/3 + knockout rounds) with match counts,
    for the By-matchday view's selector."""
    conn = db.connect()
    try:
        keys = _matchday_keys(conn)
        rows = conn.execute("SELECT id, status FROM matches").fetchall()
    finally:
        conn.close()
    mds = {}
    for r in rows:
        key, label, order = keys[r["id"]]
        d = mds.setdefault(key, {"key": key, "label": label, "_order": order,
                                 "matches": 0, "finished": 0})
        d["matches"] += 1
        if r["status"] == "finished":
            d["finished"] += 1
    ordered = sorted(mds.values(), key=lambda d: d["_order"])
    for i, d in enumerate(ordered, 1):
        d["index"] = i
        d.pop("_order", None)
    return {"matchdays": ordered}


@app.get("/api/matchday/{key}")
def api_matchday(key: str, view: str = "ghosts", user=Depends(auth.current_user)):
    """teamtip-style 'rankings by matchday' grid. `key` is a round key: g1/g2/g3
    (each team's 1st/2nd/3rd group game) or a KO round (R32/R16/QF/SF/3RD/FINAL).
    `view` selects which accounts to show: ghosts | local | both (see ACCOUNT_VIEWS).
    Returns the selected players × every match in that round, with each predicted
    scoreline and the points it earned.

    Per-match reveal: a column's tips stay hidden until that match's kickoff has
    passed (same rule as /api/match/{id}/tips), so nobody can peek beforehand."""
    if view not in ACCOUNT_VIEWS:
        view = "both"
    conn = db.connect()
    try:
        keys = _matchday_keys(conn)
        mids = [mid for mid, (k, _l, _o) in keys.items() if k == key]
        if not mids:
            raise HTTPException(404, f"unknown matchday {key}")
        qmarks = ",".join("?" * len(mids))
        matches = [dict(m) for m in conn.execute(
            "SELECT id,grp,round,match_no,home_team,away_team,home_ref,away_ref,"
            f"kickoff_et,status,home_score,away_score FROM matches "
            f"WHERE id IN ({qmarks}) ORDER BY kickoff_et, match_no", mids)]
        label = keys[mids[0]][1]
        results = _finished_results(conn)
        user_tips = _user_match_tips(conn)
        usernames = _approved_usernames(conn)
        member_tips = db.get_member_tips(conn)
        members = db.get_members(conn)
    finally:
        conn.close()

    now = datetime.datetime.utcnow()
    # which matches are revealed (kickoff passed or finished)
    cols = []
    for m in matches:
        revealed = m["status"] == "finished" or now >= _kickoff_utc(m["kickoff_et"])
        cols.append({
            "id": m["id"], "group": m["grp"], "round": m["round"],
            "home": m["home_team"] or m["home_ref"],
            "away": m["away_team"] or m["away_ref"],
            "kickoff_et": m["kickoff_et"], "status": m["status"],
            "result": (f"{m['home_score']}-{m['away_score']}"
                       if m["status"] == "finished" else None),
            "revealed": revealed,
        })
    revealed_ids = {c["id"] for c in cols if c["revealed"]}

    def row_for(subject_tips, name, kind, is_self):
        cells, total = {}, 0
        for c in cols:
            mid = c["id"]
            if mid not in revealed_ids:
                cells[mid] = None                      # hidden until kickoff
                continue
            t = subject_tips.get(mid)
            if not t:
                cells[mid] = {"tip": None}
                continue
            cell = {"tip": f"{t[0]}-{t[1]}"}
            if mid in results:
                cls = SC.classify(t[0], t[1], *results[mid])
                cell["points"] = SC.POINTS[cls]
                cell["outcome"] = cls
                total += SC.POINTS[cls]
            cells[mid] = cell
        return {"name": name, "kind": kind, "is_self": is_self,
                "cells": cells, "matchday_points": total}

    ghost_names = {(mb["display_name"] or "").strip().lower() for mb in members}
    rows = []
    if view in ("local", "both"):
        for uid, uname in usernames.items():
            if view == "both" and (uname or "").strip().lower() in ghost_names:
                continue  # dedupe: keep the teamtip ghost, drop the app-user copy
            rows.append(row_for(user_tips.get(uid, {}), uname, "user", uid == user["id"]))
    if view in ("ghosts", "both"):
        for mb in members:
            mkey = (mb["betgame_id"], mb["fk_user"])
            rows.append(row_for(member_tips.get(mkey, {}), mb["display_name"], "teamtip", False))

    # order players by points earned in this matchday (revealed games only)
    rows.sort(key=lambda r: (-r["matchday_points"], r["name"].lower()))
    return {"key": key, "label": label, "view": view, "matches": cols, "rows": rows,
            "scheme": SC.scheme()}


@app.post("/api/reset")
def api_reset(admin=Depends(auth.require_admin)):
    db.init_and_seed(force=True)
    conn = db.connect()
    R.resolve(conn)
    conn.close()
    return {"ok": True}
