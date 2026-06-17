"""SQLite persistence. DB lives at $WC_DB (default /data/wc2026.db) which is a
Docker volume, so results, accounts and tips survive container restarts."""
import datetime
import json
import os
import pathlib
import sqlite3

DB_PATH = os.environ.get("WC_DB", "/data/wc2026.db")
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
  name   TEXT PRIMARY KEY,
  grp    TEXT NOT NULL,
  iso    TEXT NOT NULL,
  rating INTEGER NOT NULL,
  host   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matches (
  id         TEXT PRIMARY KEY,
  stage      TEXT NOT NULL,
  grp        TEXT,
  round      TEXT NOT NULL,
  match_no   INTEGER,
  home_ref   TEXT NOT NULL,
  away_ref   TEXT NOT NULL,
  home_team  TEXT,
  away_team  TEXT,
  kickoff_et TEXT NOT NULL,
  venue      TEXT, city TEXT,
  home_score INTEGER, away_score INTEGER,
  home_pens  INTEGER, away_pens  INTEGER,
  winner     TEXT,
  status     TEXT NOT NULL DEFAULT 'scheduled'
);
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  username   TEXT UNIQUE NOT NULL,
  pw_hash    TEXT NOT NULL,
  is_admin   INTEGER NOT NULL DEFAULT 0,
  role       TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
  approved   INTEGER NOT NULL DEFAULT 0,      -- admin must acknowledge new signups
  api_token  TEXT,                            -- personal token for the prediction API
  timezone   TEXT,                            -- IANA tz for displaying kick-off times
  slot_ratings TEXT,                          -- JSON [24] ints 1-5: how good each local hour is
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT,
  expires    TEXT
);
CREATE TABLE IF NOT EXISTS provider_creds (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL,
  data_enc    BLOB NOT NULL,
  updated_at  TEXT,
  PRIMARY KEY (user_id, provider_id)
);
CREATE TABLE IF NOT EXISTS tips (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL,
  match_id    TEXT NOT NULL,
  home        INTEGER NOT NULL,
  away        INTEGER NOT NULL,
  updated_at  TEXT,
  PRIMARY KEY (user_id, provider_id, match_id)
);
CREATE TABLE IF NOT EXISTS prediction_overrides (
  match_id   TEXT PRIMARY KEY,
  data       TEXT NOT NULL,        -- JSON: subset of prediction fields to override (global/admin)
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS user_pred_overrides (
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  match_id   TEXT NOT NULL,
  data       TEXT NOT NULL,        -- JSON: this user's own prediction for the match
  updated_at TEXT,
  PRIMARY KEY (user_id, match_id)
);
CREATE TABLE IF NOT EXISTS teamtip_members (
  betgame_id    TEXT NOT NULL,
  fk_user       INTEGER NOT NULL,
  display_name  TEXT NOT NULL,
  owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  -- teamtip's own authoritative ranking numbers (source of truth for ghosts)
  tt_points     INTEGER,              -- points_total from /bg_v_ranking_16
  tt_exact      INTEGER,
  tt_goaldiff   INTEGER,
  tt_tendency   INTEGER,
  tt_betcount   INTEGER,
  tt_position   INTEGER,
  first_seen    TEXT,
  last_seen     TEXT,
  PRIMARY KEY (betgame_id, fk_user)
);
CREATE TABLE IF NOT EXISTS member_tips (
  betgame_id  TEXT NOT NULL,
  fk_user     INTEGER NOT NULL,
  match_id    TEXT NOT NULL,
  home        INTEGER NOT NULL,
  away        INTEGER NOT NULL,
  updated_at  TEXT,
  PRIMARY KEY (betgame_id, fk_user, match_id)
);
CREATE TABLE IF NOT EXISTS standings_snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  taken_at     TEXT NOT NULL,        -- ISO UTC timestamp of the snapshot
  matchday     INTEGER NOT NULL,     -- # of finished matches at snapshot time
  subject_kind TEXT NOT NULL,        -- 'user' | 'teamtip'
  subject_id   TEXT NOT NULL,        -- users.id (str) or 'betgame:fk_user'
  display_name TEXT NOT NULL,
  points       INTEGER NOT NULL,
  exact        INTEGER NOT NULL,
  goaldiff     INTEGER NOT NULL,
  tendency     INTEGER NOT NULL,
  betcount     INTEGER NOT NULL,
  rank         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn, table):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _migrate(conn):
    """Pre-create migrations. Legacy single-user `tips` (PK match_id, no user_id)
    is parked aside so the new multi-user `tips` can be created, then its rows are
    re-homed onto the admin user in _ensure_admin()."""
    if _table_exists(conn, "tips") and "user_id" not in _columns(conn, "tips"):
        conn.execute("ALTER TABLE tips RENAME TO _tips_legacy")
        conn.commit()


def _migrate_users(conn):
    """Add role/approved columns to a pre-existing users table (created before
    the approval workflow) and grandfather existing accounts in as approved."""
    if not _table_exists(conn, "users"):
        return
    cols = _columns(conn, "users")
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        conn.execute("UPDATE users SET role='admin' WHERE is_admin=1")
    if "approved" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE users SET approved=1")  # existing users predate approval
    if "api_token" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN api_token TEXT")
    if "timezone" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
    if "slot_ratings" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN slot_ratings TEXT")
    conn.commit()


def _migrate_members(conn):
    """Add teamtip's authoritative ranking columns to a pre-existing
    teamtip_members table (created before native points were stored)."""
    if not _table_exists(conn, "teamtip_members"):
        return
    cols = _columns(conn, "teamtip_members")
    for col in ("tt_points", "tt_exact", "tt_goaldiff", "tt_tendency",
                "tt_betcount", "tt_position"):
        if col not in cols:
            conn.execute(f"ALTER TABLE teamtip_members ADD COLUMN {col} INTEGER")
    conn.commit()


def _seed(conn):
    seeded = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    if seeded:
        return
    teams = json.loads((DATA_DIR / "teams.json").read_text(encoding="utf-8"))
    fixtures = json.loads((DATA_DIR / "fixtures.json").read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO teams(name,grp,iso,rating,host) VALUES(?,?,?,?,?)",
        [(t["name"], t["group"], t["iso"], t["rating"], int(t["host"])) for t in teams],
    )
    for m in fixtures:
        ht = m["home_ref"] if m["stage"] == "group" else None
        at = m["away_ref"] if m["stage"] == "group" else None
        conn.execute(
            """INSERT INTO matches(id,stage,grp,round,match_no,home_ref,away_ref,
               home_team,away_team,kickoff_et,venue,city) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["id"], m["stage"], m["group"], m["round"], m["match_no"],
             m["home_ref"], m["away_ref"], ht, at, m["kickoff_et"], m["venue"], m["city"]),
        )
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_update','never')")
    conn.commit()


def _ensure_admin(conn):
    """Create the initial admin from env on first run, then fold in any legacy
    single-user data: old `tips` rows and env-configured teamtip credentials."""
    from . import auth  # local import: auth imports db

    if auth.user_count(conn) > 0:
        return
    username = os.environ.get("ADMIN_USER", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    admin_id = auth.create_user(conn, username, password, role="admin", approved=1)

    # Re-home legacy tips onto the admin user under the teamtip provider.
    if _table_exists(conn, "_tips_legacy"):
        conn.execute(
            """INSERT OR IGNORE INTO tips(user_id,provider_id,match_id,home,away,updated_at)
               SELECT ?, 'teamtip', match_id, home, away, updated_at FROM _tips_legacy""",
            (admin_id,))
        conn.execute("DROP TABLE _tips_legacy")

    _import_legacy_teamtip(conn, admin_id)
    conn.commit()


def _import_legacy_teamtip(conn, user_id):
    # Import legacy env teamtip credentials so existing setups keep working.
    token = os.environ.get("TEAMTIP_TOKEN")
    if token:
        set_provider_creds(conn, user_id, "teamtip", {
            "token": token,
            "fk_user": os.environ.get("TEAMTIP_USER", "000000"),
            "fk_betgame": os.environ.get("TEAMTIP_BETGAME", "000000"),
            "base": os.environ.get("TEAMTIP_BASE", ""),
        })
    conn.commit()


def _ensure_admin_active(conn):
    """Self-heal: the account named by ADMIN_USER is always an approved admin,
    so no migration glitch or accidental state can lock the operator out."""
    username = os.environ.get("ADMIN_USER", "admin")
    conn.execute(
        "UPDATE users SET role='admin', is_admin=1, approved=1 WHERE username=?",
        (username,))
    conn.commit()


def init_and_seed(force=False):
    pathlib.Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    if force:
        conn.executescript(
            "DROP TABLE IF EXISTS matches; DROP TABLE IF EXISTS teams; "
            "DROP TABLE IF EXISTS meta; DROP TABLE IF EXISTS tips;")
        conn.commit()
    _migrate(conn)
    conn.executescript(SCHEMA)
    _migrate_users(conn)
    _migrate_members(conn)
    _seed(conn)
    _ensure_admin(conn)
    _ensure_admin_active(conn)
    conn.close()


# ---------- provider credentials ----------
def set_provider_creds(conn, user_id, provider_id, data: dict):
    from .crypto import encrypt_dict
    blob = encrypt_dict(data)
    conn.execute(
        """INSERT INTO provider_creds(user_id,provider_id,data_enc,updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(user_id,provider_id) DO UPDATE SET
             data_enc=excluded.data_enc, updated_at=excluded.updated_at""",
        (user_id, provider_id, blob, _now()))
    conn.commit()


def get_provider_creds(conn, user_id, provider_id) -> dict:
    from .crypto import decrypt_dict
    row = conn.execute(
        "SELECT data_enc FROM provider_creds WHERE user_id=? AND provider_id=?",
        (user_id, provider_id)).fetchone()
    return decrypt_dict(row["data_enc"]) if row else {}


def delete_provider_creds(conn, user_id, provider_id):
    conn.execute("DELETE FROM provider_creds WHERE user_id=? AND provider_id=?",
                 (user_id, provider_id))
    conn.commit()


def configured_provider_ids(conn, user_id) -> list[str]:
    return [r["provider_id"] for r in conn.execute(
        "SELECT provider_id FROM provider_creds WHERE user_id=? ORDER BY provider_id",
        (user_id,))]


# ---------- ratings (prediction lever) ----------
def get_ratings(conn):
    return [dict(r) for r in conn.execute(
        "SELECT name,grp,iso,rating,host FROM teams ORDER BY grp,name")]


def update_ratings(conn, ratings: dict) -> tuple[list, list]:
    """ratings = {team_name: new_rating}. Updates existing teams only.
    Returns (updated_names, unknown_names)."""
    known = {r["name"] for r in conn.execute("SELECT name FROM teams")}
    updated, unknown = [], []
    for name, val in ratings.items():
        if name in known:
            conn.execute("UPDATE teams SET rating=? WHERE name=?", (int(val), name))
            updated.append(name)
        else:
            unknown.append(name)
    conn.commit()
    return updated, unknown


# ---------- per-match prediction overrides ----------
def get_overrides(conn) -> dict:
    return {r["match_id"]: json.loads(r["data"])
            for r in conn.execute("SELECT match_id,data FROM prediction_overrides")}


def set_override(conn, match_id, data: dict):
    conn.execute(
        """INSERT INTO prediction_overrides(match_id,data,updated_at) VALUES(?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET data=excluded.data,
             updated_at=excluded.updated_at""",
        (match_id, json.dumps(data), _now()))
    conn.commit()


def delete_override(conn, match_id):
    conn.execute("DELETE FROM prediction_overrides WHERE match_id=?", (match_id,))
    conn.commit()


# ---------- per-user prediction overrides (each user's own view) ----------
def get_user_overrides(conn, user_id) -> dict:
    return {r["match_id"]: json.loads(r["data"])
            for r in conn.execute(
                "SELECT match_id,data FROM user_pred_overrides WHERE user_id=?", (user_id,))}


def set_user_override(conn, user_id, match_id, data: dict):
    conn.execute(
        """INSERT INTO user_pred_overrides(user_id,match_id,data,updated_at) VALUES(?,?,?,?)
           ON CONFLICT(user_id,match_id) DO UPDATE SET data=excluded.data,
             updated_at=excluded.updated_at""",
        (user_id, match_id, json.dumps(data), _now()))
    conn.commit()


def delete_user_override(conn, user_id, match_id):
    conn.execute("DELETE FROM user_pred_overrides WHERE user_id=? AND match_id=?",
                 (user_id, match_id))
    conn.commit()


# ---------- per-user display prefs (timezone + kick-off slot ratings) ----------
def get_prefs(conn, user_id) -> tuple:
    r = conn.execute("SELECT timezone,slot_ratings FROM users WHERE id=?",
                     (user_id,)).fetchone()
    if not r:
        return None, None
    slots = json.loads(r["slot_ratings"]) if r["slot_ratings"] else None
    return r["timezone"], slots


def set_timezone(conn, user_id, tz: str):
    conn.execute("UPDATE users SET timezone=? WHERE id=?", (tz, user_id))
    conn.commit()


def set_slot_ratings(conn, user_id, slots):
    conn.execute("UPDATE users SET slot_ratings=? WHERE id=?",
                 (json.dumps(slots), user_id))
    conn.commit()


# ---------- teamtip group members (read-only ghost accounts) ----------
def upsert_member(conn, betgame_id, fk_user, display_name, owner_user_id,
                  ranking=None):
    """Insert/update a ghost member. `ranking` (optional) carries teamtip's own
    authoritative numbers: dict with points/exact/goaldiff/tendency/betcount/position."""
    now = _now()
    r = ranking or {}
    conn.execute(
        """INSERT INTO teamtip_members(betgame_id,fk_user,display_name,owner_user_id,
             tt_points,tt_exact,tt_goaldiff,tt_tendency,tt_betcount,tt_position,
             first_seen,last_seen)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(betgame_id,fk_user) DO UPDATE SET
             display_name=excluded.display_name,
             owner_user_id=excluded.owner_user_id,
             tt_points=excluded.tt_points, tt_exact=excluded.tt_exact,
             tt_goaldiff=excluded.tt_goaldiff, tt_tendency=excluded.tt_tendency,
             tt_betcount=excluded.tt_betcount, tt_position=excluded.tt_position,
             last_seen=excluded.last_seen""",
        (str(betgame_id), int(fk_user), display_name, owner_user_id,
         r.get("points"), r.get("exact"), r.get("goaldiff"), r.get("tendency"),
         r.get("betcount"), r.get("position"), now, now))


def upsert_member_tip(conn, betgame_id, fk_user, match_id, home, away):
    conn.execute(
        """INSERT INTO member_tips(betgame_id,fk_user,match_id,home,away,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(betgame_id,fk_user,match_id) DO UPDATE SET
             home=excluded.home, away=excluded.away, updated_at=excluded.updated_at""",
        (str(betgame_id), int(fk_user), match_id, int(home), int(away), _now()))


def get_members(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT betgame_id,fk_user,display_name,tt_points,tt_exact,tt_goaldiff,"
        "tt_tendency,tt_betcount,tt_position FROM teamtip_members")]


def get_member_tips(conn) -> dict:
    """{(betgame_id, fk_user): {match_id: (home, away)}}"""
    out = {}
    for r in conn.execute(
            "SELECT betgame_id,fk_user,match_id,home,away FROM member_tips"):
        out.setdefault((r["betgame_id"], r["fk_user"]), {})[r["match_id"]] = \
            (r["home"], r["away"])
    return out


# ---------- standings snapshots (progress over the tournament) ----------
def write_snapshot(conn, matchday, rows):
    """rows = list of dicts with keys subject_kind, subject_id, display_name,
    points, exact, goaldiff, tendency, betcount, rank. One snapshot batch."""
    now = _now()
    # idempotent per matchday: replace any existing snapshot at this matchday so
    # repeated syncs on the same matchday don't pile up duplicate series points.
    conn.execute("DELETE FROM standings_snapshots WHERE matchday=?", (int(matchday),))
    conn.executemany(
        """INSERT INTO standings_snapshots(taken_at,matchday,subject_kind,subject_id,
             display_name,points,exact,goaldiff,tendency,betcount,rank)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        [(now, int(matchday), r["subject_kind"], str(r["subject_id"]),
          r["display_name"], r["points"], r["exact"], r["goaldiff"],
          r["tendency"], r["betcount"], r["rank"]) for r in rows])
    conn.commit()


def get_snapshots(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM standings_snapshots ORDER BY matchday,rank")]
