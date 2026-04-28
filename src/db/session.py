from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.config import settings
from src.db.models import Base

engine = (
    create_engine(
        settings.DATABASE_URL,
        connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000"},
        pool_pre_ping=True,
    )
    if settings.DATABASE_URL
    else None
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None

# Columns added after initial schema — safe to run every startup (IF NOT EXISTS)
_COLUMN_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS thread_ts VARCHAR",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS progress_message_ts VARCHAR",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS phase_label VARCHAR",
    "ALTER TABLE personas ADD COLUMN IF NOT EXISTS deep_research_flagged BOOLEAN DEFAULT FALSE",
    "ALTER TABLE personas ADD COLUMN IF NOT EXISTS gong_hook TEXT",
    "ALTER TABLE sequences ADD COLUMN IF NOT EXISTS personalization_tier VARCHAR",
]


def init_db():
    if not engine:
        raise RuntimeError("DATABASE_URL is not set")
    # Use lock_timeout='1ms' so DDL never waits for locks held by another container.
    # If create_all() can't acquire locks immediately, it raises LockNotAvailable
    # which is caught and ignored (tables already exist from previous deploys).
    with engine.connect() as conn:
        conn.execute(text("SET lock_timeout = '1ms'"))
        conn.execute(text("SET statement_timeout = '10s'"))
        try:
            Base.metadata.create_all(bind=conn)
        except Exception:
            pass  # tables likely already exist; lock contention is expected on rolling deploys
        # Apply additive column migrations — idempotent
        for sql in _COLUMN_MIGRATIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists or lock contention — safe to ignore
        # Reset session-level GUC settings so this connection is clean when returned to pool
        try:
            conn.execute(text("RESET lock_timeout"))
            conn.execute(text("RESET statement_timeout"))
            conn.commit()
        except Exception:
            pass


@contextmanager
def get_session():
    """Context manager that properly closes the session on exit. Use instead of next(get_db())."""
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is not set")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db():
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is not set")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
