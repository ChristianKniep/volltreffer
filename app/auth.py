"""Accounts + sessions.

Passwords are PBKDF2-HMAC-SHA256 (stdlib, no native deps). Sessions are random
opaque tokens stored in the ``sessions`` table and carried in an HttpOnly
cookie, so logout/expiry are server-controlled and no signing library is needed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets

from fastapi import Cookie, Depends, Header, HTTPException

from . import db

COOKIE = "wc_session"
SESSION_DAYS = 30
_PBKDF2_ROUNDS = 240_000


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------- users ----------
def create_user(conn, username: str, password: str,
                role: str = "user", approved: int = 0) -> int:
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    is_admin = 1 if role == "admin" else 0
    cur = conn.execute(
        "INSERT INTO users(username,pw_hash,is_admin,role,approved,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (username.strip(), hash_password(password), is_admin, role, int(approved), now))
    conn.commit()
    return cur.lastrowid


def get_user_by_name(conn, username: str):
    return conn.execute("SELECT * FROM users WHERE username=?",
                        (username.strip(),)).fetchone()


def user_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def admin_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]


def list_users(conn):
    return conn.execute(
        "SELECT id,username,role,approved,created_at FROM users "
        "ORDER BY approved ASC, datetime(created_at) ASC").fetchall()


def set_user_approved(conn, uid: int, approved: int) -> None:
    conn.execute("UPDATE users SET approved=? WHERE id=?", (int(approved), uid))
    conn.commit()


def set_user_role(conn, uid: int, role: str) -> None:
    conn.execute("UPDATE users SET role=?, is_admin=? WHERE id=?",
                 (role, 1 if role == "admin" else 0, uid))
    conn.commit()


def delete_user(conn, uid: int) -> None:
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()


# ---------- personal API tokens ----------
def rotate_api_token(conn, user_id: int) -> str:
    token = "vt_" + secrets.token_urlsafe(24)
    conn.execute("UPDATE users SET api_token=? WHERE id=?", (token, user_id))
    conn.commit()
    return token


def clear_api_token(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET api_token=NULL WHERE id=?", (user_id,))
    conn.commit()


def get_user_by_token(conn, token: str | None):
    if not token:
        return None
    return conn.execute("SELECT * FROM users WHERE api_token=?", (token,)).fetchone()


# ---------- sessions ----------
def start_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.utcnow()
    exp = now + dt.timedelta(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO sessions(token,user_id,created_at,expires) VALUES(?,?,?,?)",
        (token, user_id, now.isoformat(timespec="seconds") + "Z",
         exp.isoformat(timespec="seconds") + "Z"))
    conn.commit()
    return token


def end_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()


def _session_user(conn, token: str | None):
    if not token:
        return None
    row = conn.execute(
        "SELECT s.expires AS expires, u.* FROM sessions s "
        "JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
    if not row:
        return None
    if row["expires"] and row["expires"] < dt.datetime.utcnow().isoformat() + "Z":
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    return row


# ---------- FastAPI dependencies ----------
def current_account(authorization: str | None = Header(default=None),
                    wc_session: str | None = Cookie(default=None)):
    """Resolve the caller from a personal `Authorization: Bearer <api_token>`
    (for agents/automation acting as that user) or the session cookie. 401 if
    neither resolves. The account may still be awaiting approval."""
    conn = db.connect()
    try:
        user = None
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() == "bearer" and value.strip():
                user = get_user_by_token(conn, value.strip())
        if not user:
            user = _session_user(conn, wc_session)
    finally:
        conn.close()
    if not user:
        raise HTTPException(401, "not authenticated")
    return user


def current_user(user=Depends(current_account)):
    """Require a logged-in AND admin-approved user; 403 while pending."""
    if not user["approved"]:
        raise HTTPException(403, "account pending admin approval")
    return user


def require_admin(user=Depends(current_user)):
    """Require an approved admin; 403 otherwise."""
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def automation_or_admin(authorization: str | None = Header(default=None),
                        wc_session: str | None = Cookie(default=None)):
    """Allow machine clients (agents) via `Authorization: Bearer <AUTOMATION_TOKEN>`,
    or a logged-in admin via the session cookie. Returns a principal dict."""
    token = os.environ.get("AUTOMATION_TOKEN")
    if token and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), token):
            return {"id": None, "username": "automation", "role": "admin",
                    "approved": 1, "automation": True}
    # fall back to an admin session
    conn = db.connect()
    try:
        user = _session_user(conn, wc_session)
    finally:
        conn.close()
    if not user:
        raise HTTPException(401, "not authenticated")
    if not user["approved"]:
        raise HTTPException(403, "account pending admin approval")
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def optional_user(wc_session: str | None = Cookie(default=None)):
    conn = db.connect()
    try:
        return _session_user(conn, wc_session)
    finally:
        conn.close()


def cookie_kwargs() -> dict:
    """Cookie flags; Secure is opt-in via COOKIE_SECURE for HTTPS deploys."""
    return {
        "key": COOKIE, "httponly": True, "samesite": "lax",
        "secure": os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
        "max_age": SESSION_DAYS * 86400, "path": "/",
    }
