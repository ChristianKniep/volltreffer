#!/usr/bin/env python3
"""
Auto-submit this app's model predictions as tips in your teamtip round.

Targets teamtip's actual API (reverse-engineered from a HAR capture):

  matches : GET  /schedule/matches_139_16.json      (id, matchdate[CEST], team names DE)
  my tips : GET  /bg_bet?select=...&fk_betgame=eq.<bg>&fk_user=eq.<uid>
  save    : PUT  /bg_bet?fk_user=eq.<uid>&fk_match=eq.<mid>&fk_betgame=eq.<bg>
            body {"fk_user":<uid>,"fk_match":<mid>,"goalshome":h,"goalsguest":g,"fk_betgame":<bg>}

It reads predicted scorelines from your running app (GET /api/state), maps them
to teamtip match IDs by kick-off date + team, and PUTs one tip per match.
DRY RUN by default - add --submit to actually send.

AUTH: teamtip uses a session cookie. Grab it once from your browser
(DevTools - Network - any /bg_bet request - copy the 'Cookie' request header,
or right-click - Copy as cURL) and pass it via --cookie or TEAMTIP_COOKIE.
Use only your own account; this rides private endpoints that can change anytime,
so confirm it's within teamtip's ToS and your pool's fair-play rules.

    pip install requests
    export TEAMTIP_COOKIE='...paste cookie header...'
    python teamtip_submit.py                       # dry run
    python teamtip_submit.py --only-new            # skip matches already tipped
    python teamtip_submit.py --submit              # really send
"""
import argparse
import datetime as dt
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("This tool needs 'requests':  pip install requests")

# --- discovered defaults (override per round/user via CLI/env) ---
BASE = os.environ.get("TEAMTIP_BASE", "https://teamtip.net")
SCHEDULE_URL = os.environ.get("TEAMTIP_SCHEDULE", f"{BASE}/schedule/matches_139_16.json")
FK_USER = int(os.environ.get("TEAMTIP_USER", "454596"))
FK_BETGAME = int(os.environ.get("TEAMTIP_BETGAME", "150936"))

# teamtip lists teams in German; map to our English names
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


def canon(name):
    return (name or "").strip().lower().replace("&", "and")


def en_pair(home_de, away_de):
    """German names -> canonical English pair, or None if either is a placeholder."""
    h, a = DE2EN.get(home_de), DE2EN.get(away_de)
    if not h or not a:
        return None
    return frozenset((canon(h), canon(a)))


def cest_date(et_str):
    """Our fixtures store US-Eastern; teamtip stores CEST (= ET + 6h). Match on CEST date."""
    et = dt.datetime.strptime(et_str, "%Y-%m-%d %H:%M")
    return (et + dt.timedelta(hours=6)).strftime("%Y-%m-%d")


# ---------------- our app ----------------
def predictions_from_app(app_base):
    state = requests.get(f"{app_base}/api/state", timeout=20).json()
    preds = {}
    for m in state["matches"]:
        if m["status"] == "finished" or not m.get("prediction") or not m["home"] or not m["away"]:
            continue
        date = cest_date(m["kickoff_et"])
        pair = frozenset((canon(m["home"]), canon(m["away"])))
        preds[(date, pair)] = {
            "h": m["prediction"]["score_home"], "g": m["prediction"]["score_away"],
            "label": f'{m["home"]} {m["prediction"]["score_home"]}-{m["prediction"]["score_away"]} {m["away"]}',
        }
    return preds


# ---------------- teamtip ----------------
def teamtip_matches(session):
    rows = session.get(SCHEDULE_URL, timeout=20).json()
    out = []
    for r in rows:
        pair = en_pair(r["team_home"], r["team_guest"])
        if pair is None:
            continue  # knockout slot not yet a real team
        out.append({"id": r["id"], "date": str(r["matchdate"])[:10], "pair": pair,
                    "home": r["team_home"], "away": r["team_guest"]})
    return out


def existing_tips(session):
    url = (f"{BASE}/bg_bet?select=fk_user,fk_match,goalshome,goalsguest"
           f"&fk_betgame=eq.{FK_BETGAME}&fk_user=eq.{FK_USER}")
    rows = session.get(url, timeout=20).json()
    return {row["fk_match"] for row in rows}


def submit_tip(session, fk_match, gh, gg):
    url = f"{BASE}/bg_bet?fk_user=eq.{FK_USER}&fk_match=eq.{fk_match}&fk_betgame=eq.{FK_BETGAME}"
    body = {"fk_user": FK_USER, "fk_match": fk_match,
            "goalshome": gh, "goalsguest": gg, "fk_betgame": FK_BETGAME}
    r = session.put(url, json=body, timeout=20)
    r.raise_for_status()
    return r.status_code


def make_session(cookie, headers):
    s = requests.Session()
    s.headers.update({"accept": "application/json", "content-type": "application/json",
                      "origin": BASE, "referer": f"{BASE}/bet"})
    if cookie:
        s.headers["cookie"] = cookie
    for hv in headers or []:
        k, _, v = hv.partition(":")
        s.headers[k.strip()] = v.strip()
    return s


def build_plan(tt_matches, preds):
    planned, unmatched = [], []
    for tm in tt_matches:
        p = preds.get((tm["date"], tm["pair"]))
        if not p:  # tolerate a CEST/ET off-by-one by trying the pair on adjacent days
            for delta in (-1, 1):
                d = (dt.date.fromisoformat(tm["date"]) + dt.timedelta(days=delta)).isoformat()
                p = preds.get((d, tm["pair"]))
                if p:
                    break
        (planned if p else unmatched).append((tm, p))
    return planned, unmatched


def main():
    ap = argparse.ArgumentParser(description="Submit model predictions to a teamtip round.")
    ap.add_argument("--app", default=os.environ.get("APP_BASE", "http://localhost:8000"))
    ap.add_argument("--cookie", default=os.environ.get("TEAMTIP_COOKIE"))
    ap.add_argument("--header", action="append", help="extra 'Name: value' header (repeatable)")
    ap.add_argument("--only-new", action="store_true", help="skip matches you've already tipped")
    ap.add_argument("--submit", action="store_true", help="actually send (default is dry run)")
    args = ap.parse_args()

    s = make_session(args.cookie, args.header)
    if not args.cookie and not args.header:
        print("! No --cookie/TEAMTIP_COOKIE given. The schedule may load, but reading your "
              "tips and submitting will fail until you provide your session cookie.\n")

    preds = predictions_from_app(args.app)
    print(f"Predictions available: {len(preds)} upcoming matches.")
    tt = teamtip_matches(s)
    print(f"teamtip fixtures with real teams: {len(tt)}.")

    already = set()
    if args.cookie or args.header:
        try:
            already = existing_tips(s)
            print(f"Already tipped: {len(already)} matches.")
        except Exception as e:
            print(f"Couldn't read existing tips ({e}); continuing without --only-new filter.")

    planned, unmatched = build_plan(tt, preds)
    if args.only_new:
        planned = [(tm, p) for tm, p in planned if tm["id"] not in already]

    print(f"\n{'SUBMITTING' if args.submit else 'WOULD SUBMIT'} {len(planned)} tips:")
    for tm, p in planned:
        flag = "  (already tipped)" if tm["id"] in already and not args.only_new else ""
        print(f"  match {tm['id']}  {tm['date']}  {p['label']:40} -> {p['h']}-{p['g']}{flag}")
    if unmatched:
        print(f"\n{len(unmatched)} teamtip fixtures had no prediction (KO slots or name mismatch):")
        for tm, _ in unmatched[:8]:
            print(f"  - {tm['date']} {tm['home']} v {tm['away']}")
        if len(unmatched) > 8:
            print(f"  ... +{len(unmatched)-8} more")

    if not args.submit:
        print("\nDry run. Re-run with --submit to send.")
        return
    if not (args.cookie or args.header):
        sys.exit("Refusing to submit without auth - provide --cookie/TEAMTIP_COOKIE.")
    ok = 0
    for tm, p in planned:
        try:
            submit_tip(s, tm["id"], p["h"], p["g"]); ok += 1
        except Exception as e:
            print(f"  ! match {tm['id']} failed: {e}")
    print(f"\nSubmitted {ok}/{len(planned)} tips.")


if __name__ == "__main__":
    main()
