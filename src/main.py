import logging
import os
import uuid
from datetime import datetime

import sentry_sdk
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import settings
from src.agents.normalizer import InputNormalizerAgent, RepRequest
from src.agents.discovery import PersonaDiscoveryAgent
from src.agents.scorer import ScorerAgent
from src.agents.generator import SequenceGeneratorAgent
from src.db.session import init_db, get_db
from src.db.models import Session, WorkflowEvent, Persona, Sequence
from src.integrations.slack_blocks import (
    confirmation_card,
    clarification_card,
    persona_list_card,
    sequence_step_card,
    sequence_brief_card,
)

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
discovery = PersonaDiscoveryAgent()
scorer = ScorerAgent()
generator = SequenceGeneratorAgent()

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
    thread_ts = body["message"]["ts"]

    log_event(session_id, "intent_confirmed", 1, user_id, {})

    say(
        text="Running persona discovery... give me a moment.",
        thread_ts=thread_ts,
    )

    # Load session to get account name + persona filter
    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found. Please start a new request.", thread_ts=thread_ts)
            return

        account_name = session.account_name
        normalized = session.normalized_request or {}
        persona_filter = normalized.get("persona_filter")

        # Run persona discovery
        personas = discovery.discover(
            session_id=session_id,
            account_name=account_name,
            persona_filter=persona_filter,
        )

        if not personas:
            say(
                text=f"I couldn't find any contacts at *{account_name}* in Apollo. "
                     "Check that the account name is correct or try a different company name.",
                thread_ts=thread_ts,
            )
            log_event(session_id, "discovery_empty", 2, user_id, {"account_name": account_name})
            return

        # Persist personas to DB
        for p in personas:
            persona_row = Persona(
                id=p["id"],
                session_id=session_id,
                apollo_id=p.get("apollo_id"),
                first_name=p["first_name"],
                last_name=p["last_name"],
                title=p["title"],
                email=p.get("email"),
                linkedin_url=p.get("linkedin_url"),
                account_name=p["account_name"],
                persona_type=p["persona_type"],
                seniority=p["seniority"],
                outreach_lane=p["outreach_lane"],
                priority_score=p["priority_score"],
                linkedin_signals=p.get("linkedin_signals", []),
                status="discovered",
            )
            db.merge(persona_row)
        session.phase = 2
        db.commit()

        log_event(session_id, "discovery_complete", 2, user_id, {"persona_count": len(personas)})

        # Post persona selection card
        say(
            blocks=persona_list_card(personas, session_id),
            text=f"Found {len(personas)} personas at {account_name}. Select who to include:",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"Phase 2 failed for session {session_id}: {e}", exc_info=True)
        say(
            text="Something went wrong during persona discovery. Please try again.",
            thread_ts=thread_ts,
        )


@app.action("confirm_personas")
def handle_confirm_personas(ack, body, say):
    """Checkpoint 1 — rep selects which personas to include."""
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    # Collect selected persona IDs from checkbox state
    selected_ids = []
    state_values = body.get("state", {}).get("values", {})
    for block_values in state_values.values():
        for action_id, action_data in block_values.items():
            if action_id.startswith("approve_persona_"):
                for opt in action_data.get("selected_options", []):
                    selected_ids.append(opt["value"])

    if not selected_ids:
        say(
            text="Please select at least one persona before confirming.",
            thread_ts=thread_ts,
        )
        return

    try:
        db = next(get_db())
        all_personas = db.query(Persona).filter(Persona.session_id == session_id).all()
        for p in all_personas:
            p.approved_by_rep = p.id in selected_ids
            p.status = "approved" if p.id in selected_ids else "rejected"
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.phase = 3
        db.commit()

        log_event(session_id, "personas_approved", 2, user_id, {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
        })

        say(
            text=f"Got it — {len(selected_ids)} persona(s) confirmed. Scoring and generating sequences...",
            thread_ts=thread_ts,
        )

        # Load session for account context
        session = db.query(Session).filter(Session.id == session_id).first()
        account_name = session.account_name if session else ""
        normalized = (session.normalized_request or {}) if session else {}
        account_description = normalized.get("company_description")

        # Load approved personas
        approved_personas = (
            db.query(Persona)
            .filter(Persona.session_id == session_id, Persona.status == "approved")
            .all()
        )
        personas_dicts = [
            {
                "id": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "title": p.title,
                "seniority": p.seniority,
                "persona_type": p.persona_type,
                "outreach_lane": p.outreach_lane,
                "priority_score": p.priority_score,
                "linkedin_signals": p.linkedin_signals or [],
                "gong_hook": p.gong_hook if hasattr(p, "gong_hook") else None,
                "account_name": p.account_name,
            }
            for p in approved_personas
        ]

        # Score + value-map
        scored_personas = scorer.score(personas_dicts, account_description=account_description)

        # Update DB with scoring results
        for sp in scored_personas:
            db_persona = db.query(Persona).filter(Persona.id == sp["id"]).first()
            if db_persona:
                db_persona.priority_score = sp["priority_score"]
                db_persona.score_reasoning = sp.get("score_reasoning")
                db_persona.value_driver = sp.get("value_driver")
        db.commit()

        # Generate sequences
        sequences = []
        for sp in scored_personas:
            seq = generator.generate(
                persona=sp,
                account_name=account_name,
                account_description=account_description,
                rep_name="Your Rep",  # TODO: pull from Slack profile
                session_id=session_id,
            )
            sequences.append(seq)

            # Persist sequence
            seq_row = Sequence(
                id=seq["id"],
                session_id=session_id,
                persona_id=sp["id"],
                lane=seq["lane"],
                status="draft",
                steps=seq["steps"],
                edit_history=[],
            )
            db.add(seq_row)

        if session:
            session.phase = 4
        db.commit()

        log_event(session_id, "sequences_generated", 3, user_id, {
            "sequence_count": len(sequences),
        })

        # Post each sequence for rep review (Checkpoint 2)
        for seq in sequences:
            persona_match = next((p for p in scored_personas if p["id"] == seq["persona_id"]), {})
            name = f"{persona_match.get('first_name', '')} {persona_match.get('last_name', '')}".strip()
            lane = seq["lane"]
            step_count = len(seq["steps"])

            say(
                text=f"Sequence for {name} ({lane} lane, {step_count} steps) — review below:",
                thread_ts=thread_ts,
            )

            # Post each step
            for step in seq["steps"]:
                for block in sequence_step_card(step, name, seq["id"]):
                    pass  # blocks collected below

            # Post all steps as one message per sequence
            all_blocks = []
            all_blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": f"Sequence: {name} ({lane} lane)"},
            })
            for step in seq["steps"]:
                all_blocks.extend(sequence_step_card(step, name, seq["id"]))

            all_blocks.append({
                "type": "actions",
                "block_id": f"approve_sequence_{seq['id']}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve Sequence"},
                        "style": "primary",
                        "action_id": "approve_sequence",
                        "value": seq["id"],
                    }
                ],
            })

            say(
                blocks=all_blocks,
                text=f"Sequence for {name}",
                thread_ts=thread_ts,
            )

    except Exception as e:
        logger.error(f"confirm_personas failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong saving your selections. Please try again.", thread_ts=thread_ts)


@app.action("approve_sequence")
def handle_approve_sequence(ack, body, say):
    """Checkpoint 2 — rep approves a generated sequence. Delivers the final brief."""
    ack()
    sequence_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    try:
        db = next(get_db())
        seq = db.query(Sequence).filter(Sequence.id == sequence_id).first()
        if not seq:
            say(text="Sequence not found.", thread_ts=thread_ts)
            return

        seq.status = "approved"
        session = db.query(Session).filter(Session.id == seq.session_id).first()
        if session:
            session.phase = 5

        persona = db.query(Persona).filter(Persona.id == seq.persona_id).first()
        db.commit()

        log_event(seq.session_id, "sequence_approved", 4, user_id, {"sequence_id": sequence_id})

        if not persona:
            say(text="Sequence approved, but persona record not found for brief.", thread_ts=thread_ts)
            return

        persona_dict = {
            "first_name": persona.first_name,
            "last_name": persona.last_name,
            "title": persona.title,
            "account_name": persona.account_name,
        }
        sequence_dict = {
            "lane": seq.lane,
            "steps": seq.steps or [],
        }

        say(
            blocks=sequence_brief_card(sequence_dict, persona_dict),
            text=f"Sequence brief for {persona.first_name} {persona.last_name} — ready to paste into Apollo.",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"approve_sequence failed for {sequence_id}: {e}", exc_info=True)
        say(text="Something went wrong delivering the sequence brief.", thread_ts=thread_ts)


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


@app.action("approve_step")
def handle_approve_step(ack, body, say):
    """Acknowledge step approval — full edit loop built in Phase 5."""
    ack()
    value = body["actions"][0]["value"]  # "sequence_id:step_number"
    say(
        text=f"Step approved.",
        thread_ts=body["message"]["ts"],
    )


@app.action("edit_step")
def handle_edit_step(ack, body, say):
    """Prompt rep to reply with edited copy — full modal edit built in Phase 5."""
    ack()
    value = body["actions"][0]["value"]  # "sequence_id:step_number"
    say(
        text="Reply here with your edited copy for this step and I'll update it.",
        thread_ts=body["message"]["ts"],
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
