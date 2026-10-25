"""Bearer-token authentication and role checks.

Local default: CODEMIND_AUTH_REQUIRED=false -> every request acts as the local admin (bind to localhost only).
When enabled, `Authorization: Bearer <token>` must be CODEMIND_ADMIN_TOKEN (admin) or the token issued for a user. Only
SHA-256 hashes of user tokens are stored. Roles: viewer < developer < admin.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.session import get_db
from app.models import User

ROLE_RANK = {"viewer": 1, "developer": 2, "admin": 3}


@dataclass
class Principal:
    username: str
    role: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "cm_" + secrets.token_urlsafe(32)


def current_principal(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Principal:
    s = get_settings()
    if not s.auth_required:
        return Principal("local", "admin")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("missing bearer token")
    token = authorization[7:].strip()
    if s.admin_token and hmac.compare_digest(token, s.admin_token):
        return Principal("admin", "admin")
    user = db.execute(select(User).where(User.token_hash == hash_token(token))).scalar_one_or_none()
    if user is None or not user.token_hash:
        raise UnauthorizedError("invalid token")
    return Principal(user.username, user.role)


def require_role(min_role: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if ROLE_RANK[p.role] < ROLE_RANK[min_role]:
            raise ForbiddenError(f"requires role '{min_role}' (you are '{p.role}')")
        return p

    return dep


viewer = require_role("viewer")
developer = require_role("developer")
admin = require_role("admin")
