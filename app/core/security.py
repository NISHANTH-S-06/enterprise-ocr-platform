from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Dict, Iterable, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.models.enums import UserStatus
from app.models.enums import UserRole

settings = get_settings()
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=True)
_VALID_ROLES = {UserRole.SUPERADMIN.value, UserRole.ADMIN.value, UserRole.OPERATOR.value, UserRole.REVIEWER.value}
_ROLE_ALIASES = {"viewer": UserRole.REVIEWER.value}


def hash_password(p: str) -> str: return _pwd.hash(p)
def verify_password(p: str, h: str) -> bool: return _pwd.verify(p, h)


def create_access_token(data: Dict[str, Any]) -> str:
    payload = {**data, "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "type": "access"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token", headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    p = decode_token(creds.credentials)
    if p.get("type") != "access" or not p.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid token")
    token_role = p.get("role")
    if not token_role:
        raise HTTPException(status_code=401, detail="Invalid token")
    token_role = _ROLE_ALIASES.get(str(token_role), str(token_role))
    if token_role not in _VALID_ROLES:
        raise HTTPException(status_code=403, detail="Role not permitted")
    try:
        user_id = uuid.UUID(str(p["sub"]))
        user = await db.get(User, user_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not user or user.is_deleted or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status_code=401, detail="Invalid token")
    role = _ROLE_ALIASES.get(user.role, user.role)
    if role not in _VALID_ROLES or role != token_role:
        raise HTTPException(status_code=403, detail="Role not permitted")
    return {"user_id": str(user.id), "role": role, "org_id": str(user.org_id) if user.org_id else None}


def require_roles(*allowed: UserRole | str):
    allowed_roles = {role.value if isinstance(role, UserRole) else str(role) for role in allowed}
    allowed_roles.add(UserRole.ADMIN.value) if UserRole.ADMIN.value not in allowed_roles else None
    allowed_roles.add(UserRole.SUPERADMIN.value)

    async def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return current_user

    return _dependency
