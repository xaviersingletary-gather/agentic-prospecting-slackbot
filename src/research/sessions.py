"""In-memory research-session store (spec §1.3, project state V1).

Survives a single interaction callback but is lost on restart — acceptable
for V1. V2 moves this to Redis.
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ResearchSession:
    session_id: str
    rep_id: str
    account_name: str
    personas: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


_SESSIONS: Dict[str, ResearchSession] = {}


def create_session(rep_id: str, account_name: str) -> ResearchSession:
    sid = uuid.uuid4().hex
    sess = ResearchSession(session_id=sid, rep_id=rep_id, account_name=account_name)
    _SESSIONS[sid] = sess
    return sess


def get_session(session_id: str) -> Optional[ResearchSession]:
    return _SESSIONS.get(session_id)


def update_personas(session_id: str, personas: List[str]) -> Optional[ResearchSession]:
    sess = _SESSIONS.get(session_id)
    if sess is None:
        return None
    sess.personas = list(personas)
    return sess


def _reset_for_tests() -> None:
    _SESSIONS.clear()
