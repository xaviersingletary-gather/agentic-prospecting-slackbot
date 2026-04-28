from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.config import settings
from src.db.models import Base

engine = (
    create_engine(
        settings.DATABASE_URL,
        connect_args={"connect_timeout": 10},
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
    # Run all DDL in AUTOCOMMIT mode so each statement commits immediately.
    # This means no transaction is open between statements — no table locks are held
    # that could block DML from handler connections running concurrently.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        try:
            Base.metadata.create_all(bind=conn)
        except Exception:
            pass  # tables already exist on rolling deploys
        for sql in _COLUMN_MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception:
                pass  # column already exists — safe to ignore


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
