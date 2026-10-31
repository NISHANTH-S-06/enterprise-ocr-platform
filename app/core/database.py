from __future__ import annotations
from collections.abc import AsyncGenerator
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings

@compiles(JSONB, 'sqlite')
def compile_jsonb_sqlite(type_, compiler, **kw):
    return 'JSON'

@compiles(INET, 'sqlite')
def compile_inet_sqlite(type_, compiler, **kw):
    return 'VARCHAR(45)'

settings = get_settings()

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

engine_kwargs = {
    "echo": settings.DATABASE_ECHO,
    "future": True,
}
if not settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update({
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    })

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession,
    expire_on_commit=False, autoflush=False, autocommit=False,
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    import app.models  # ensure models registered with Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Auto-seed default user if no users exist
    async with AsyncSessionLocal() as db:
        from app.models.user import User
        from app.models.enums import UserRole, UserStatus
        from app.core.security import hash_password
        existing = (await db.execute(select(User))).scalars().first()
        if not existing:
            admin_user = User(
                email="admin@example.com",
                hashed_password=hash_password("Admin123!"),
                full_name="Admin User",
                role=UserRole.ADMIN.value,
                status=UserStatus.ACTIVE.value,
            )
            db.add(admin_user)
            await db.commit()


async def drop_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_db() -> None:
    await engine.dispose()

