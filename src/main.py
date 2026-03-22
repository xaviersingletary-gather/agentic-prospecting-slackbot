import logging
import os
import uuid
from datetime import datetime

import sentry_sdk
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import settings
from src.agents.normalizer import InputNormalizerAgent, RepRequest
from src.db.session import init_db, get_db
from src.db.models import Session, WorkflowEvent
from src.integrations.slack_blocks import confirmation_card, clarification_card

# Sentry — must be initialized before anything else
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=1.0,
    )

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

app = App(token=settings.SLACK_BOT_TOKEN)
normalizer = InputNormalizerAgent()

REP_ROLES = {}  # slack user_id -> "AE" | "MDR" — populated from Slack profile or config


def get_rep_role(user_id: str) -> str:
    return REP_ROLES.get(user_id, "AE")


def log_event(session_id: str, event_type: str, phase: int, rep_id: str, payload: dict):
    try:
        db = next(get_db())
        event = WorkflowEvent(
            id=str(uuid.uuid4()),
            session_id=session_id,
            event_type=event_type,
            phase=phase,
            rep_id=rep_id,
            payload=payload,
            timestamp=datetime.utcnow(),
        )
        db.add(event)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to log workflow event: {e}")


@app.message()
def handle_message(message, say, client):
    user_id = message.get("user")
    channel_id = message.get("channel")
    text = message.get("text", "").strip()

    if not text or message.get("bot_id"):
        return

    rep_role = get_rep_role(user_id)
    session_id = str(uuid.uuid4())

    logger.info(f"New prospecting request from {user_id}: {text[:80]}")

    # Run normalizer
    request = RepRequest(
        raw_message=text,
        rep_id=user_id,
        rep_role=rep_role,
        channel_id=channel_id,
        timestamp=datetime.utcnow().isoformat(),
    )
    normalized = normalizer.normalize(request)

    # Persist session
    try:
        db = next(get_db())
        session = Session(
            id=session_id,
            account_name=normalized.account_name,
            account_domain=normalized.account_domain,
            rep_id=user_id,
            rep_role=rep_role,
            channel_id=channel_id,
            phase=1,
            status="active",
            normalized_request=normalized.to_dict(),
        )
        db.add(session)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to persist session: {e}")

    log_event(session_id, "session_started", 1, user_id, {"raw_message": text})

    # Send clarification or confirmation card
    if normalized.clarification_needed:
        say(
            blocks=clarification_card(normalized.clarification_question, session_id),
            text=normalized.clarification_question,
        )
    else:
        say(
            blocks=confirmation_card(
                account_name=normalized.account_name,
                persona_filter=normalized.persona_filter,
                use_case_angle=normalized.use_case_angle,
                session_id=session_id,
            ),
            text=f"Got it — running outreach for {normalized.account_name}. Is that right?",
        )


@app.action("confirm_intent")
def handle_confirm_intent(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]

    log_event(session_id, "intent_confirmed", 1, user_id, {})

    say(
        text=f"Running persona discovery for this account. Give me a moment...",
        thread_ts=body["message"]["ts"],
    )
    # Phase 2 will be triggered here once built


@app.action("edit_intent")
def handle_edit_intent(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]
    say(
        text="No problem — tell me what to change. Which account did you want to target?",
        thread_ts=body["message"]["ts"],
    )


@app.action("submit_clarification")
def handle_submit_clarification(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    # Extract clarification input value
    state = body.get("state", {}).get("values", {})
    clarification_text = ""
    for block_values in state.values():
        for action_values in block_values.values():
            clarification_text = action_values.get("value", "")

    log_event(session_id, "intent_corrected", 1, user_id, {"clarification": clarification_text})

    # Re-run normalizer with the clarification text
    rep_role = get_rep_role(user_id)
    request = RepRequest(
        raw_message=clarification_text,
        rep_id=user_id,
        rep_role=rep_role,
        channel_id=body["channel"]["id"],
    )
    normalized = normalizer.normalize(request)

    say(
        blocks=confirmation_card(
            account_name=normalized.account_name,
            persona_filter=normalized.persona_filter,
            use_case_angle=normalized.use_case_angle,
            session_id=session_id,
        ),
        text=f"Got it — {normalized.account_name}. Is that right?",
    )


if __name__ == "__main__":
    if settings.DATABASE_URL:
        init_db()
        logger.info("Database initialized")
    else:
        logger.warning("DATABASE_URL not set — skipping DB initialization")

    logger.info(f"Starting Gather AI Prospecting Bot [{settings.ENVIRONMENT}]")
    handler = SocketModeHandler(app, settings.SLACK_APP_TOKEN)
    handler.start()
