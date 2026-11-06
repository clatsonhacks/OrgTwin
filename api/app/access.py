"""People and permissions.

Until someone sets up an admin, OrgTwin works as before: one shared workspace, optionally behind
APP_ACCESS_TOKEN. Once the first admin exists, every change needs a person:

  * admin   — everything: build the company, connect apps, invite people, give skills tools.
  * member  — create and edit skills only in the departments the admin ticked for them;
              see and run every skill; send requests to any team; approve steps in their departments.

People sign in with the personal link the admin sends them (?invite=<token>). The token goes in the
X-User-Token header; only its sha256 is stored. APP_ACCESS_TOKEN keeps working as an admin key for scripts.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException
from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, User


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    t = "ot_" + secrets.token_urlsafe(24)
    return t, _hash(t)


def enabled() -> bool:
    """People mode is on once an admin exists."""
    with SessionLocal() as s:
        return (s.scalar(select(func.count()).select_from(User)) or 0) > 0


def user_by_token(token: str) -> User | None:
    if not token:
        return None
    with SessionLocal() as s:
        u = s.scalars(select(User).where(User.token_hash == _hash(token))).first()
        if u and not u.joined:
            u.joined = True
            s.commit()
        return u


def _master(token: str) -> bool:
    return bool(settings.app_access_token) and hmac.compare_digest(token, settings.app_access_token)


class Actor:
    """Who is making this request. `user` is None in the legacy single-workspace mode or for the master key."""

    def __init__(self, user: User | None, admin: bool):
        self.user, self.admin = user, admin

    @property
    def name(self) -> str:
        return self.user.name if self.user else ("admin" if self.admin else "demo user")

    @property
    def id(self) -> int | None:
        return self.user.id if self.user else None

    def departments(self) -> list[str]:
        return list(self.user.departments or []) if self.user else []

    def can_edit(self, team_key: str) -> bool:
        return self.admin or team_key in self.departments()


def actor(x_access_token: str = Header(default=""), x_user_token: str = Header(default="")) -> Actor:
    """Any change: a signed-in person (or the master key). In legacy mode, just APP_ACCESS_TOKEN as before."""
    if not enabled():
        if settings.app_access_token and not _master(x_access_token):
            raise HTTPException(401, "missing or wrong X-Access-Token")
        return Actor(None, admin=True)
    if _master(x_access_token) or _master(x_user_token):
        return Actor(None, admin=True)
    u = user_by_token(x_user_token)
    if not u:
        raise HTTPException(401, "sign in with your invite link to make changes")
    return Actor(u, admin=u.role == "admin")


def admin(x_access_token: str = Header(default=""), x_user_token: str = Header(default="")) -> Actor:
    a = actor(x_access_token, x_user_token)
    if not a.admin:
        raise HTTPException(403, "only an admin can do this")
    return a


def viewer(x_access_token: str = Header(default=""), x_user_token: str = Header(default="")) -> Actor | None:
    """Who is looking (no error when nobody is signed in)."""
    try:
        return actor(x_access_token, x_user_token)
    except HTTPException:
        return None


def require_edit(a: Actor, team_key: str, what: str = "skills") -> None:
    if not a.can_edit(team_key):
        raise HTTPException(403, f"you can only change {what} in your own departments — ask an admin to add {team_key!r}")


def user_dict(u: User, with_token: str | None = None) -> dict:
    out = {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "departments": u.departments or [],
           "notify": u.notify or {}, "joined": u.joined}
    if with_token:
        out["token"] = with_token
        out["invite_url"] = f"{settings.public_app_url.rstrip('/')}/?invite={with_token}"
    return out


def owners_of(team_key: str) -> list[User]:
    """People who look after a department's skills: its members, else the admins."""
    with SessionLocal() as s:
        users = list(s.scalars(select(User)))
    own = [u for u in users if team_key in (u.departments or [])]
    return own or [u for u in users if u.role == "admin"]
