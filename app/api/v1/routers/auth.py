from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password, get_current_user
from app.models.user import User
from app.models.enums import LogEventType, LogLevel, UserRole, UserStatus
from app.services.audit_service import write_audit_log

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.OPERATOR


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if settings.APP_ENV == "production" and not settings.ALLOW_PUBLIC_REGISTRATION:
        raise HTTPException(status_code=403, detail="Public registration is disabled")
    existing = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if existing:
        await write_audit_log(
            db,
            action="user.register",
            event_type=LogEventType.USER_MGMT,
            status="failure",
            level=LogLevel.WARNING,
            message="Registration failed: email already registered",
            metadata={"email": req.email},
            request=request,
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Email already registered")
    if req.role not in (UserRole.OPERATOR, UserRole.REVIEWER):
        raise HTTPException(status_code=403, detail="Role not permitted for self-registration")
    user = User(email=req.email, hashed_password=hash_password(req.password),
                full_name=req.full_name, role=req.role.value, status=UserStatus.ACTIVE.value)
    db.add(user)
    await db.flush()
    await write_audit_log(
        db,
        action="user.register",
        event_type=LogEventType.USER_MGMT,
        status="success",
        user_id=user.id,
        org_id=user.org_id,
        resource_type="user",
        resource_id=user.id,
        message="User registered",
        metadata={"email": user.email, "role": user.role},
        request=request,
    )
    return {"user_id": str(user.id), "email": user.email}


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == req.email, User.is_deleted == False))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        await write_audit_log(
            db,
            action="auth.login",
            event_type=LogEventType.AUTH,
            status="failure",
            user_id=user.id if user else None,
            org_id=user.org_id if user else None,
            level=LogLevel.WARNING,
            message="Login failed: invalid credentials",
            metadata={"email": req.email},
            request=request,
        )
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.status != UserStatus.ACTIVE.value:
        await write_audit_log(
            db,
            action="auth.login",
            event_type=LogEventType.AUTH,
            status="failure",
            user_id=user.id,
            org_id=user.org_id,
            level=LogLevel.WARNING,
            message="Login failed: account inactive",
            metadata={"email": req.email, "account_status": user.status},
            request=request,
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="Account inactive")
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role,
                                  "org_id": str(user.org_id) if user.org_id else None})
    await write_audit_log(
        db,
        action="auth.login",
        event_type=LogEventType.AUTH,
        status="success",
        user_id=user.id,
        org_id=user.org_id,
        resource_type="user",
        resource_id=user.id,
        message="Login successful",
        metadata={"email": user.email, "role": user.role},
        request=request,
    )
    return TokenResponse(access_token=token)


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user
