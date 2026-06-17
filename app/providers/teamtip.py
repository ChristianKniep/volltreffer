"""teamtip.net provider.

teamtip has no public API; this rides the PostgREST-style endpoints
reverse-engineered from HAR captures:

  schedule (public) : GET /schedule/matches_139_16.json
  read own tips      : GET /bg_bet?select=fk_match,goalshome,goalsguest
                              &fk_betgame=eq.<bg>&fk_user=eq.<uid>   (needs auth)
  save/overwrite tip : PUT /bg_bet?fk_user=eq.<uid>&fk_match=eq.<mid>&fk_betgame=eq.<bg>

Credentials are per-user (entered in the Settings pane), not global env vars.
Tips are matched to our fixtures by CEST date + canonical team pair and stored
locally, so they stay visible after the bearer token later expires.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import urllib.request

from . import BetProvider, CredField, register

DEFAULT_BASE = "https://teamtip.net"
DEFAULT_SCHEDULE = "/schedule/matches_139_16.json"
DEFAULT_COMPETITION = "139"
RANKING_VIEW = "/bg_v_ranking_16"

DE2EN = {
    "Algerien": "Algeria", "Argentinien": "Argentina", "Australien": "Australia",
    "Belgien": "Belgium", "Bosnien-Herzegowina": "Bosnia & Herzegovina", "Brasilien": "Brazil",
    "Curaçao": "Curaçao", "DR Kongo": "DR Congo", "Deutschland": "Germany", "Ecuador": "Ecuador",
    "Elfenbeinküste": "Ivory Coast", "England": "England", "Frankreich": "France", "Ghana": "Ghana",
    "Haiti": "Haiti", "Irak": "Iraq", "Iran": "Iran", "Japan": "Japan", "Jordanien": "Jordan",
    "Kanada": "Canada", "Kapverdische Inseln": "Cape Verde", "Katar": "Qatar", "Kolumbien": "Colombia",
    "Kroatien": "Croatia", "Marokko": "Morocco", "Mexiko": "Mexico", "Neuseeland": "New Zealand",
    "Niederlande": "Netherlands", "Norwegen": "Norway", "Panama": "Panama", "Paraguay": "Paraguay",
    "Portugal": "Portugal", "Saudi-Arabien": "Saudi Arabia", "Schottland": "Scotland",
    "Schweden": "Sweden", "Schweiz": "Switzerland", "Spanien": "Spain", "Sénégal": "Senegal",
    "Südafrika": "South Africa", "Südkorea": "South Korea", "Tschechien": "Czechia",
    "Tunesien": "Tunisia", "Türkei": "Türkiye", "USA": "USA", "Uruguay": "Uruguay",
    "Usbekistan": "Uzbekistan", "Ägypten": "Egypt", "Österreich": "Austria",
}


def _canon(s):
    return (s or "").strip().lower().replace("&", "and")


def _auth(token):
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token
    return token


def _http(url, token=None):
    headers = {"accept": "application/json"}
    if token:
        headers["authorization"] = _auth(token)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_put(url, body, token, base):
    data = json.dumps(body).encode("utf-8")
    headers = {"accept": "application/json", "content-type": "application/json",
               "origin": base, "referer": f"{base}/bet", "authorization": _auth(token)}
    req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def _cest_date(et_str):
    et = dt.datetime.strptime(et_str, "%Y-%m-%d %H:%M")
    return (et + dt.timedelta(hours=6)).strftime("%Y-%m-%d")


def _our_match_index(conn):
    idx = {}
    for m in conn.execute("SELECT id,home_team,away_team,kickoff_et FROM matches"):
        if not m["home_team"] or not m["away_team"]:
            continue
        idx[(_cest_date(m["kickoff_et"]),
             frozenset((_canon(m["home_team"]), _canon(m["away_team"])))) ] = m["id"]
    return idx


def _tt_schedule_index(schedule):
    """teamtip match id -> (cest_date, canonical pair); skips placeholder slots."""
    out = {}
    for r in schedule:
        h, a = DE2EN.get(r["team_home"]), DE2EN.get(r["team_guest"])
        if not h or not a:
            continue
        out[r["id"]] = (str(r["matchdate"])[:10], frozenset((_canon(h), _canon(a))))
    return out


def _now():
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def token_exp(token):
    """Decode a JWT's `exp` (seconds since epoch) without verifying the signature.
    Display-only — lets the UI warn before a token lapses. Returns None on any
    parse failure."""
    if not token:
        return None
    raw = token.split()[-1] if token.lower().startswith("bearer ") else token
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return int(data["exp"]) if "exp" in data else None
    except Exception:  # noqa: BLE001
        return None


@register
class TeamtipProvider(BetProvider):
    id = "teamtip"
    label = "teamtip.net"
    blurb = ("German prediction-pool site. Needs your bearer token (copy the "
             "Authorization header from a logged-in /bg_bet request in DevTools), "
             "plus your user and betgame IDs.")
    credential_fields = [
        CredField("token", "Bearer token", type="password", secret=True,
                  placeholder="Bearer eyJ…",
                  help="DevTools → Network → any /bg_bet request → Request Headers → authorization."),
        CredField("fk_user", "User ID (fk_user)", type="text",
                  placeholder="000000"),
        CredField("fk_betgame", "Betgame / round ID (fk_betgame)", type="text",
                  placeholder="000000"),
        CredField("fk_competition", "Competition ID (fk_competition)", type="text",
                  required=False, placeholder=DEFAULT_COMPETITION,
                  help="Needed for group import. Leave blank for the default (139 = WC 2026)."),
        CredField("base", "Base URL", type="text", required=False,
                  placeholder=DEFAULT_BASE,
                  help="Leave blank for the default teamtip.net."),
    ]

    # ---- helpers ----
    @staticmethod
    def _cfg(creds):
        base = (creds.get("base") or DEFAULT_BASE).rstrip("/")
        return (creds.get("token", ""), str(creds.get("fk_user", "")),
                str(creds.get("fk_betgame", "")), base)

    def _schedule_url(self, base):
        return f"{base}{DEFAULT_SCHEDULE}"

    # ---- capabilities ----
    def validate(self, creds):
        token, uid, bg, base = self._cfg(creds)
        if not token or not uid or not bg:
            return False, "token, user ID and betgame ID are all required."
        try:
            self._http_tips(base, bg, uid, token)
            return True, "Credentials accepted — tips are readable."
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "403" in msg:
                return False, "token rejected (expired or wrong scope)."
            return False, f"check failed: {msg}"

    @staticmethod
    def _http_tips(base, bg, uid, token):
        return _http(f"{base}/bg_bet?select=fk_match,goalshome,goalsguest"
                     f"&fk_betgame=eq.{bg}&fk_user=eq.{uid}", token=token)

    def sync_tips(self, conn, user_id, creds):
        out = {"source": self.id, "synced": 0, "skipped": 0, "errors": []}
        token, uid, bg, base = self._cfg(creds)
        if not token or not uid or not bg:
            out["errors"].append("teamtip credentials incomplete.")
            return out
        try:
            schedule = _http(self._schedule_url(base))
            tips = self._http_tips(base, bg, uid, token)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "403" in msg:
                msg = "token rejected (expired?) — paste a fresh one in Settings."
            out["errors"].append(f"teamtip fetch failed: {msg}")
            return out

        tt = _tt_schedule_index(schedule)
        ours = _our_match_index(conn)
        now = _now()
        for t in tips:
            key = tt.get(t["fk_match"])
            mid = ours.get(key) if key else None
            if not mid and key:  # tolerate a date off-by-one
                for d in (-1, 1):
                    alt = ((dt.date.fromisoformat(key[0]) + dt.timedelta(days=d)).isoformat(), key[1])
                    mid = ours.get(alt)
                    if mid:
                        break
            if mid:
                _upsert_tip(conn, user_id, self.id, mid, t["goalshome"], t["goalsguest"], now)
                out["synced"] += 1
            else:
                out["skipped"] += 1
        conn.commit()
        return out

    def submit_tip(self, conn, user_id, creds, match_id, home, away):
        out = {"ok": False}
        token, uid, bg, base = self._cfg(creds)
        if not token or not uid or not bg:
            out["error"] = "teamtip credentials incomplete — set them in Settings."
            return out
        row = conn.execute(
            "SELECT id,home_team,away_team,kickoff_et,status FROM matches WHERE id=?",
            (match_id,)).fetchone()
        if not row:
            out["error"] = "match not found."
            return out
        if not row["home_team"] or not row["away_team"]:
            out["error"] = "teams not resolved yet — can't tip this match."
            return out
        if row["status"] == "finished":
            out["error"] = "match already finished."
            return out
        try:
            schedule = _http(self._schedule_url(base))
        except Exception as e:  # noqa: BLE001
            out["error"] = f"teamtip schedule fetch failed: {e}"
            return out
        pair = frozenset((_canon(row["home_team"]), _canon(row["away_team"])))
        fk_match = self._find_match(schedule, _cest_date(row["kickoff_et"]), pair)
        if not fk_match:
            out["error"] = "no matching teamtip fixture (KO placeholder or name mismatch)."
            return out
        home, away = int(home), int(away)
        url = f"{base}/bg_bet?fk_user=eq.{uid}&fk_match=eq.{fk_match}&fk_betgame=eq.{bg}"
        body = {"fk_user": int(uid), "fk_match": fk_match,
                "goalshome": home, "goalsguest": away, "fk_betgame": int(bg)}
        try:
            status = _http_put(url, body, token, base)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "403" in msg:
                msg = "token rejected (expired or lacks write scope?)."
            out["error"] = f"teamtip submit failed: {msg}"
            return out
        _upsert_tip(conn, user_id, self.id, match_id, home, away, _now())
        conn.commit()
        out.update(ok=True, fk_match=fk_match, status=status, home=home, away=away)
        return out

    # ---- group import (all betgame members, read-only) ----
    @staticmethod
    def _http_all_tips(base, bg, token):
        """Every member's scorelines for the betgame (no fk_user filter).
        teamtip's row-level security permits co-members to read these."""
        return _http(f"{base}/bg_bet?select=fk_user,fk_match,goalshome,goalsguest"
                     f"&fk_betgame=eq.{bg}&limit=15000", token=token)

    @staticmethod
    def _http_ranking(base, bg, comp, token):
        return _http(f"{base}{RANKING_VIEW}?select=fk_user,user_name,points_total,"
                     f"exact,goaldiff,tendency,betcount,position"
                     f"&fk_betgame=eq.{bg}&fk_competition=eq.{comp}"
                     f"&order=position.asc&limit=15000", token=token)

    def sync_group(self, conn, owner_user_id, creds):
        """Import every betgame member (names + raw scorelines) as read-only
        ghost accounts, reusing the owner's stored token."""
        from .. import db  # local import avoids a circular import at module load
        out = {"source": self.id, "members": 0, "tips": 0, "skipped": 0, "errors": []}
        token, _uid, bg, base = self._cfg(creds)
        comp = str(creds.get("fk_competition") or DEFAULT_COMPETITION)
        if not token or not bg:
            out["errors"].append("teamtip credentials incomplete (token + betgame ID).")
            return out
        try:
            schedule = _http(self._schedule_url(base))
            ranking = self._http_ranking(base, bg, comp, token)
            tips = self._http_all_tips(base, bg, token)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "403" in msg:
                msg = "token rejected (expired?) — paste a fresh one in Settings."
            out["errors"].append(f"teamtip group fetch failed: {msg}")
            return out

        for m in ranking:
            db.upsert_member(conn, bg, m["fk_user"],
                             (m.get("user_name") or f"user {m['fk_user']}").strip(),
                             owner_user_id,
                             ranking={
                                 "points": m.get("points_total"),
                                 "exact": m.get("exact"),
                                 "goaldiff": m.get("goaldiff"),
                                 "tendency": m.get("tendency"),
                                 "betcount": m.get("betcount"),
                                 "position": m.get("position"),
                             })
            out["members"] += 1

        tt = _tt_schedule_index(schedule)
        ours = _our_match_index(conn)
        for t in tips:
            if t.get("goalshome") is None or t.get("goalsguest") is None:
                continue
            key = tt.get(t["fk_match"])
            mid = ours.get(key) if key else None
            if not mid and key:  # tolerate a date off-by-one
                for d in (-1, 1):
                    alt = ((dt.date.fromisoformat(key[0]) + dt.timedelta(days=d)).isoformat(),
                           key[1])
                    mid = ours.get(alt)
                    if mid:
                        break
            if mid:
                db.upsert_member_tip(conn, bg, t["fk_user"], mid,
                                     t["goalshome"], t["goalsguest"])
                out["tips"] += 1
            else:
                out["skipped"] += 1
        conn.commit()
        out["betgame_id"] = str(bg)
        return out

    @staticmethod
    def _find_match(schedule, cest_date, pair):
        cand = {}
        for r in schedule:
            h, a = DE2EN.get(r["team_home"]), DE2EN.get(r["team_guest"])
            if not h or not a:
                continue
            cand[(str(r["matchdate"])[:10], frozenset((_canon(h), _canon(a))))] = r["id"]
        mid = cand.get((cest_date, pair))
        if mid:
            return mid
        for d in (-1, 1):
            alt = ((dt.date.fromisoformat(cest_date) + dt.timedelta(days=d)).isoformat(), pair)
            if alt in cand:
                return cand[alt]
        return None


def _upsert_tip(conn, user_id, provider_id, match_id, home, away, now):
    conn.execute(
        """INSERT INTO tips(user_id,provider_id,match_id,home,away,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(user_id,provider_id,match_id) DO UPDATE SET
             home=excluded.home, away=excluded.away, updated_at=excluded.updated_at""",
        (user_id, provider_id, match_id, int(home), int(away), now))
