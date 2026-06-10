#!/usr/bin/env python3
"""Dependency-free client for a *user's own* volltreffer predictions.

Authenticates as you with your Personal API token (Settings tab) and updates the
match predictions shown in *your* view only — it never changes the shared model
or your betting tips.

Config via env (or pass explicitly):
    VOLLTREFFER_URL     base URL, default http://localhost:8000
    VOLLTREFFER_TOKEN   your personal token (starts with vt_)

Library:
    from my_predictions import Client
    c = Client()
    for m in c.upcoming():                         # matches you can still predict
        print(m["id"], m["home"], m["away"], m["prediction"]["score_home"], "-", ...)
    c.set("G12", score_home=2, score_away=1, rationale="Star striker back from injury.")
    c.clear("G12")                                 # revert this match to the model
    c.mine()                                        # your current overrides

CLI:
    python my_predictions.py upcoming
    python my_predictions.py mine
    python my_predictions.py set G12 --home 2 --away 1 --rationale "Striker fit again."
    python my_predictions.py clear G12
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FIELDS = {"score_home", "score_away", "p_home", "p_draw", "p_away",
          "adv_home", "adv_away", "favored", "confidence", "rationale"}


class Client:
    def __init__(self, base_url=None, token=None):
        self.base = (base_url or os.environ.get("VOLLTREFFER_URL", "http://localhost:8000")).rstrip("/")
        self.token = token or os.environ.get("VOLLTREFFER_TOKEN", "")
        if not self.token:
            print("warning: VOLLTREFFER_TOKEN is empty — generate one on the Settings tab.",
                  file=sys.stderr)

    def _req(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json", "authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')}")

    def state(self):
        """Full app state as you see it (predictions reflect your own overrides)."""
        return self._req("GET", "/api/state")

    def upcoming(self):
        """Matches with both teams known that aren't finished — i.e. predictable."""
        return [m for m in self.state()["matches"]
                if m["home"] and m["away"] and m["status"] != "finished"]

    def mine(self):
        """Your active per-match overrides: {match_id: {fields...}}."""
        return self._req("GET", "/api/my-predictions")["overrides"]

    def set(self, match_id, **fields):
        data = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
        if not data:
            raise SystemExit(f"set needs at least one of {sorted(FIELDS)}")
        return self._req("POST", f"/api/match/{match_id}/my-prediction", data)

    def clear(self, match_id):
        return self._req("DELETE", f"/api/match/{match_id}/my-prediction")


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Update your own volltreffer predictions")
    ap.add_argument("--url"); ap.add_argument("--token")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("upcoming"); sub.add_parser("mine"); sub.add_parser("state")
    p = sub.add_parser("set"); p.add_argument("match_id")
    p.add_argument("--home", type=int, required=True); p.add_argument("--away", type=int, required=True)
    p.add_argument("--favored", choices=["home", "draw", "away"]); p.add_argument("--rationale")
    p = sub.add_parser("clear"); p.add_argument("match_id")
    args = ap.parse_args(argv)

    c = Client(args.url, args.token)
    if args.cmd == "state":
        out = c.state()
    elif args.cmd == "upcoming":
        out = [{"id": m["id"], "home": m["home"], "away": m["away"],
                "kickoff": m["local_date"] + " " + m["local_time"],
                "prediction": f'{m["prediction"]["score_home"]}-{m["prediction"]["score_away"]}',
                "yours": bool(m["prediction"].get("override_source") == "you")}
               for m in c.upcoming()]
    elif args.cmd == "mine":
        out = c.mine()
    elif args.cmd == "set":
        out = c.set(args.match_id, score_home=args.home, score_away=args.away,
                    favored=args.favored, rationale=args.rationale)
    elif args.cmd == "clear":
        out = c.clear(args.match_id)
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    _main()
