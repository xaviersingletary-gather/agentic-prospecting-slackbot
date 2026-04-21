import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Boolean, Integer, Text, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def generate_uuid():
    return str(uuid.uuid4())


class CompanyResearch(Base):
    __tablename__ = "company_research"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, nullable=False)
    account_name = Column(String)
    is_public_company = Column(Boolean)
    facility_count = Column(Integer)
    facility_count_note = Column(String)
    total_sqft_estimate = Column(Integer)
    sqft_source = Column(String)
    board_initiatives = Column(JSON, default=list)    # [{title, summary, source}]
    company_priorities = Column(JSON, default=list)   # [str]
    trigger_events = Column(JSON, default=list)       # [{description, source, date, relevance}]
    automation_vendors = Column(JSON, default=list)   # [{vendor_name, category, deployment_status, source}]
    exception_tax = Column(JSON)                      # {total_sqft, pallet_positions, annual_savings_usd, ...}
    research_gaps = Column(JSON, default=list)        # [str]
    documents_used = Column(JSON, default=list)       # [{doc_type, source_url, filing_period}]
    raw_research_text = Column(Text)                  # full compiled research for debugging
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactResearch(Base):
    __tablename__ = "contact_research"

    id = Column(String, primary_key=True, default=generate_uuid)
    persona_id = Column(String, nullable=False)
    session_id = Column(String)
    current_role_tenure = Column(String)
    prior_roles = Column(JSON, default=list)          # [{title, company, duration}]
    recent_linkedin = Column(JSON, default=list)      # [{type, content, date}]
    speaking_activity = Column(String)
    research_gaps = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    account_name = Column(String, nullable=False)
    account_domain = Column(String)
    rep_id = Column(String, nullable=False)
    rep_role = Column(String)                    # AE | MDR
    channel_id = Column(String)
    thread_ts = Column(String)                   # Slack thread timestamp for the active workflow
    progress_message_ts = Column(String)         # ts of the live-updating progress/research message
    phase = Column(Integer, default=1)
    phase_label = Column(String)                 # human-readable phase label for resume UX
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
    deep_research_flagged = Column(Boolean, default=False)
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
