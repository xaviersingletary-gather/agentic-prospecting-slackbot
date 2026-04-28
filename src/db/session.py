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
    # Use a single connection with timeouts so DDL never hangs waiting for locks
    # (e.g. when old container is still running during a rolling deploy)
    with engine.connect() as conn:
        conn.execute(text("SET lock_timeout = '8s'"))
        conn.execute(text("SET statement_timeout = '15s'"))
        # create_all accepts a Connection in SQLAlchemy 2.x
        Base.metadata.create_all(bind=conn)
        # Apply additive column migrations — idempotent
        for sql in _COLUMN_MIGRATIONS:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists or table doesn't exist yet


def get_db():
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is not set")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
