import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Boolean, Integer, Text, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def generate_uuid():
    return str(uuid.uuid4())


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    account_name = Column(String, nullable=False)
    account_domain = Column(String)
    rep_id = Column(String, nullable=False)
    rep_role = Column(String)                    # AE | MDR
    channel_id = Column(String)
    thread_ts = Column(String)                   # Slack thread timestamp for the active workflow
    phase = Column(Integer, default=1)
    status = Column(String, default="active")    # active | completed | cancelled
    normalized_request = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Persona(Base):
    __tablename__ = "personas"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, nullable=False)
    apollo_id = Column(String)
    status = Column(String, default="discovered")    # discovered | approved | rejected
    first_name = Column(String)
    last_name = Column(String)
    title = Column(String)
    seniority = Column(String)                   # C-Suite | SVP | VP | Director | Manager
    persona_type = Column(String)                # TDM | ODM | FS | IT | Safety
    linkedin_url = Column(String)
    email = Column(String)
    account_name = Column(String)
    account_domain = Column(String)
    priority_score = Column(String)              # High | Medium | Low
    score_reasoning = Column(Text)
    outreach_lane = Column(String)               # AE | MDR
    linkedin_signals = Column(JSON, default=list)
    gong_hook = Column(Text)
    value_driver = Column(JSON)
    approved_by_rep = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


class Sequence(Base):
    __tablename__ = "sequences"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, nullable=False)
    persona_id = Column(String, nullable=False)
    lane = Column(String)                        # AE | MDR
    status = Column(String, default="draft")    # draft | rep_review | approved | delivered
    steps = Column(JSON, default=list)
    edit_history = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String)
    agent_name = Column(String)
    phase = Column(Integer)
    account = Column(String)
    rep_id = Column(String)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    input_json = Column(JSON)
    output_json = Column(JSON)
    signals_json = Column(JSON, default=list)
    errors_json = Column(JSON, default=list)
    status = Column(String)                      # success | partial | failed


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String)
    event_type = Column(String)                  # session_started | intent_confirmed | persona_approved | etc.
    phase = Column(Integer)
    rep_id = Column(String)
    payload = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)
