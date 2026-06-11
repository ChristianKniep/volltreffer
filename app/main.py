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


def _leaderboard(conn, me_id):
    names = _approved_usernames(conn)
    results = _finished_results(conn)
    tips = _user_match_tips(conn)
    rows = []
    for uid, username in names.items():
        agg = {"points": 0, "exact": 0, "goaldiff": 0, "tendency": 0, "miss": 0, "tips": 0}
        for mid, (th, ta) in tips.get(uid, {}).items():
            if mid not in results:
                continue
            cls = SC.classify(th, ta, *results[mid])
            agg[cls] += 1
            agg["points"] += SC.POINTS[cls]
            agg["tips"] += 1
        rows.append({"user_id": uid, "username": username, "is_self": uid == me_id, **agg})
    rows.sort(key=lambda r: (-r["points"], -r["exact"], -r["goaldiff"],
                             -r["tendency"], r["username"].lower()))
    # standard competition ranking (ties share a rank)
    rank = 0
    prev = None
    for i, r in enumerate(rows, 1):
        keyv = (r["points"], r["exact"], r["goaldiff"], r["tendency"])
        if keyv != prev:
            rank = i
            prev = keyv
        r["rank"] = rank
    return rows


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
    return FileResponse(str(STATIC / "index.html"))


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
    return {**cls.describe(), "configured": bool(saved), "values": summary}


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
    finally:
        conn.close()
    return {"ok": True, "valid": ok, "message": msg}


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
def api_leaderboard(user=Depends(auth.current_user)):
    conn = db.connect()
    try:
        rows = _leaderboard(conn, user["id"])
        finished = conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE status='finished'").fetchone()["c"]
    finally:
        conn.close()
    return {"scheme": SC.scheme(), "scored_matches": finished, "standings": rows}


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
        summary["providers"] = synced
    finally:
        conn.close()
    return summary


@app.post("/api/teamtip/sync")
def api_teamtip_sync(user=Depends(auth.current_user)):
    return providers_sync("teamtip", user)


@app.post("/api/reset")
def api_reset(admin=Depends(auth.require_admin)):
    db.init_and_seed(force=True)
    conn = db.connect()
    R.resolve(conn)
    conn.close()
    return {"ok": True}
