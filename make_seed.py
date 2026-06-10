#!/usr/bin/env python3
"""Generate seed JSON (teams + fixtures) for the WC2026 app.

Fixture/venue/flag data is reused from the existing poster generator
(../wc2026/build.py) so we never retype the 104-match schedule. Power
ratings are FIFA April-2026 ranking points where published, and informed
estimates (same scale) for teams outside the published top 20.
"""
import importlib.util, json, os, pathlib

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE.parent / "wc2026" / "build.py"

spec = importlib.util.spec_from_file_location("poster_build", BUILD)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

# ---- research-calibrated power ratings (FIFA points scale, ~April 2026) ----
# Top-20 are the published FIFA ranking points; the rest are estimates on the
# same scale, cross-checked against title/advancement odds (Spain & France
# co-favourites; Norway boosted by Haaland; hosts get a separate model bonus).
RATING = {
    'France':1877,'Spain':1876,'Argentina':1875,'England':1826,'Portugal':1764,
    'Brazil':1761,'Netherlands':1758,'Morocco':1756,'Belgium':1735,'Germany':1730,
    'Croatia':1717,'Colombia':1693,'Senegal':1689,'Mexico':1681,'USA':1673,
    'Uruguay':1673,'Japan':1660,'Switzerland':1649,'Norway':1639,'Iran':1630,
    'Ecuador':1567,'South Korea':1569,'Austria':1556,'Sweden':1556,'Türkiye':1560,
    'Algeria':1507,'Canada':1531,'Scotland':1503,'Czechia':1500,'Tunisia':1499,
    'Egypt':1518,'Ivory Coast':1492,'Ghana':1483,'Bosnia & Herzegovina':1483,
    'Paraguay':1481,'DR Congo':1462,'South Africa':1450,'Qatar':1438,'Panama':1430,
    'Saudi Arabia':1430,'Uzbekistan':1422,'Iraq':1416,'Jordan':1402,'Australia':1500,
    'Cape Verde':1380,'Curaçao':1350,'Haiti':1330,'New Zealand':1330,
}
HOSTS = {'USA', 'Mexico', 'Canada'}

teams = []
for g, names in b.GROUP_TEAMS.items():
    for n in names:
        teams.append({
            "name": n, "group": g, "iso": b.CODE[n],
            "rating": RATING.get(n, 1450), "host": n in HOSTS,
        })

# ---- matches ----
matches = []
gid = 0
for g, ms in b.GROUP_MATCHES.items():
    for (h, a, ets, venue, city) in ms:
        gid += 1
        matches.append({
            "id": f"G{gid:02d}", "stage": "group", "group": g, "round": "GROUP",
            "match_no": None, "home_ref": h, "away_ref": a,
            "kickoff_et": ets, "venue": venue, "city": city,
        })
for num, (rnd, sa, sb, ets, venue, city) in b.KO.items():
    matches.append({
        "id": f"M{num}", "stage": "ko", "group": None, "round": rnd,
        "match_no": num, "home_ref": sa, "away_ref": sb,
        "kickoff_et": ets, "venue": venue, "city": city,
    })

out = HERE / "app" / "data"
out.mkdir(parents=True, exist_ok=True)
(out / "teams.json").write_text(json.dumps(teams, ensure_ascii=False, indent=2))
(out / "fixtures.json").write_text(json.dumps(matches, ensure_ascii=False, indent=2))
print(f"wrote {len(teams)} teams, {len(matches)} matches")
