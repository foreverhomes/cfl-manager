"""Logins: Sign in with Google, a signed session cookie, and who may do what.

Google's sign-in button sends you to Google, which posts an ID token back to /auth/google. The
token is checked against Google's keys and this app's client id, and its email becomes the
session, kept in a signed cookie. Every request looks that email up in `users`, so a new team, a role change or a
removal takes effect on the next request.

Anyone with a Google account can ask to join: they're recorded as `pending` until the admin
approves them and links their team. Emails in CFL_ADMIN_EMAILS are admins without approval.

Without GOOGLE_CLIENT_ID there are no logins and every request acts as the admin, as the app
always worked locally. The API refuses that on Vercel.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import Settings
from .db import Connection, row, transaction
from .sync import now_iso

COOKIE = "cfl_session"
SESSION_DAYS = 30
ROLES = ("gm", "admin")
STATUSES = ("pending", "active")
# A GM may run the dashboard's "Refresh rosters & stats", which also brings the standings and this
# week's matchup scores up to date. A full sync is the admin's.
GM_SYNC_RESOURCES = frozenset({"rosters", "standings", "matchups", "stats", "match", "bios"})


@dataclass(frozen=True)
class User:
    email: str
    name: Optional[str]
    team_id: Optional[str]
    is_admin: bool
    status: str = "active"          # pending users can only see that they're waiting

    def public(self, auth: bool = True) -> Dict[str, Any]:
        return {"email": self.email, "name": self.name, "team_id": self.team_id, "is_admin": self.is_admin,
                "status": self.status, "auth": auth}


def local_admin(settings: Settings) -> User:
    """Who every request is when logins aren't set up (on your own computer). With no users to
    look up, "your" team is FANTRAX_MY_TEAM_ID, as for the CLI."""
    return User("local", None, settings.my_team_id, True)


def can_edit_team(user: User, team_id: Optional[str]) -> bool:
    """Admins edit any team; a GM edits their own team's contracts and sends its draft picks."""
    return user.is_admin or (user.team_id is not None and team_id == user.team_id)


def can_edit_player(user: User, team_id: Optional[str]) -> bool:
    """Admins edit any player's contract; a GM edits players on their own team."""
    return can_edit_team(user, team_id)


# --------------------------------------------------------------------------- Google + cookie


def verify_google_credential(credential: str, client_id: str) -> Dict[str, Any]:
    """The claims in a Google ID token, once its signature, issuer, expiry and audience check out."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    info = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id,
                                        clock_skew_in_seconds=10)
    if not info.get("email") or not info.get("email_verified"):
        raise ValueError("Google didn't confirm this account's email address")
    return info


def _serializer(secret: str):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(secret, salt="cfl-session")


def make_session(email: str, secret: str) -> str:
    return _serializer(secret).dumps({"email": email})


def read_session(token: Optional[str], secret: str) -> Optional[str]:
    """The email in a session cookie, or None if it's missing, forged or older than SESSION_DAYS."""
    from itsdangerous import BadSignature
    if not token:
        return None
    try:
        return _serializer(secret).loads(token, max_age=SESSION_DAYS * 86400).get("email")
    except (BadSignature, AttributeError):
        return None


# --------------------------------------------------------------------------- users


def load_user(conn: Connection, settings: Settings, email: str) -> Optional[User]:
    """The signed-in person, or None if they have no access (never asked, or were removed)."""
    r = row(conn, "SELECT * FROM users WHERE email=?", (email,))
    always_admin = email in settings.admin_emails
    if r is None and not always_admin:
        return None
    r = r or {}
    is_admin = always_admin or r.get("role") == "admin"
    # Everyone's team is the one linked in the Admin tab, admins included.
    return User(email, r.get("name"), r.get("team_id"), is_admin, "active" if is_admin else r.get("status", "pending"))


def record_login(conn: Connection, settings: Settings, claims: Dict[str, Any]) -> User:
    """Note a sign-in. Someone new is added as pending (an admin from CFL_ADMIN_EMAILS as active)."""
    email = claims["email"].strip().lower()
    name = claims.get("name")
    ts = now_iso()
    with transaction(conn):
        if row(conn, "SELECT email FROM users WHERE email=?", (email,)):
            conn.execute("UPDATE users SET name=COALESCE(?, name), last_login_at=? WHERE email=?", (name, ts, email))
        else:
            admin = email in settings.admin_emails
            conn.execute("INSERT INTO users (email, name, team_id, role, status, created_at, last_login_at) "
                         "VALUES (?, ?, NULL, ?, ?, ?, ?)",
                         (email, name, "admin" if admin else "gm", "active" if admin else "pending", ts, ts))
    return load_user(conn, settings, email)
