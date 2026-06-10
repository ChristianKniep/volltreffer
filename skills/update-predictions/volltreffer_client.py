#!/usr/bin/env python3
"""Dependency-free client for volltreffer's prediction API.

Lets any agent harness read the current model inputs and write them back:
 - team ratings  (the main lever; the Poisson model recomputes everything)
 - per-match prediction overrides (pin an explicit scoreline / probabilities)

Auth: an automation token, sent as `Authorization: Bearer <token>`.
Config via env (or pass explicitly):
    VOLLTREFFER_URL     base URL, default http://localhost:8000
    AUTOMATION_TOKEN    must match the server's AUTOMATION_TOKEN

Usage as a library:
    from volltreffer_client import Client
    c = Client()
    data = c.get_ratings()                      # {ratings, model, overrides}
    c.put_ratings({"Norway": 1700, "Spain": 1885})
    c.set_override("G01", score_home=2, score_away=1, rationale="Hosts roll.")
    c.clear_override("G01")
    state = c.get_state()                        # fixtures + results + predictions

Usage from the CLI:
    python volltreffer_client.py get-ratings
    python volltreffer_client.py put-ratings '{"Spain": 1885, "Norway": 1700}'
    python volltreffer_client.py set-override G01 --home 2 --away 1 --rationale "Hosts roll."
    python volltreffer_client.py clear-override G01
    python volltreffer_client.py get-state
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OVERRIDE_FIELDS = {"score_home", "score_away", "p_home", "p_draw", "p_away",
                   "adv_home", "adv_away", "favored", "confidence", "rationale"}


class Client:
    def __init__(self, base_url=None, token=None):
        self.base = (base_url or os.environ.get("VOLLTREFFER_URL", "http://localhost:8000")).rstrip("/")
        self.token = token or os.environ.get("AUTOMATION_TOKEN", "")

    def _req(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"{method} {path} -> HTTP {e.code}: {detail}")

    # --- reads ---
    def get_ratings(self):
        """{'ratings': [{name,grp,iso,rating,host}], 'model': {...}, 'overrides': {...}}"""
        return self._req("GET", "/api/ratings")

    def get_state(self):
        """Full app state: fixtures, results, standings, current predictions/tips."""
        return self._req("GET", "/api/state")

    # --- writes ---
    def put_ratings(self, ratings: dict):
        """ratings = {team_name: int}. Unknown names are reported, not created."""
        return self._req("PUT", "/api/ratings", {"ratings": ratings})

    def set_override(self, match_id: str, **fields):
        data = {k: v for k, v in fields.items() if k in OVERRIDE_FIELDS and v is not None}
        if not data:
            raise SystemExit(f"set_override needs at least one of {sorted(OVERRIDE_FIELDS)}")
        return self._req("POST", f"/api/match/{match_id}/prediction", data)

    def clear_override(self, match_id: str):
        return self._req("DELETE", f"/api/match/{match_id}/prediction")


def _main(argv=None):
    ap = argparse.ArgumentParser(description="volltreffer prediction API client")
    ap.add_argument("--url"); ap.add_argument("--token")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("get-ratings")
    sub.add_parser("get-state")
    p = sub.add_parser("put-ratings"); p.add_argument("json", help='e.g. \'{"Spain":1885}\'')
    p = sub.add_parser("set-override"); p.add_argument("match_id")
    p.add_argument("--home", type=int); p.add_argument("--away", type=int)
    p.add_argument("--favored", choices=["home", "draw", "away"])
    p.add_argument("--rationale")
    p = sub.add_parser("clear-override"); p.add_argument("match_id")
    args = ap.parse_args(argv)

    c = Client(args.url, args.token)
    if args.cmd == "get-ratings":
        out = c.get_ratings()
    elif args.cmd == "get-state":
        out = c.get_state()
    elif args.cmd == "put-ratings":
        out = c.put_ratings(json.loads(args.json))
    elif args.cmd == "set-override":
        out = c.set_override(args.match_id, score_home=args.home, score_away=args.away,
                             favored=args.favored, rationale=args.rationale)
    elif args.cmd == "clear-override":
        out = c.clear_override(args.match_id)
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    _main()
