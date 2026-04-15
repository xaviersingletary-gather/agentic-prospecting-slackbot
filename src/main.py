import logging
import os
import re
import sys
import uuid
from datetime import datetime

# Ensure project root is in sys.path so 'src' is importable regardless of how
# Railway (or any other host) invokes this file.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import sentry_sdk
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import settings
from src.agents.normalizer import InputNormalizerAgent, RepRequest
from src.agents.discovery import PersonaDiscoveryAgent
from src.agents.scorer import ScorerAgent
from src.agents.generator import SequenceGeneratorAgent
from src.agents.editor import SequenceEditorAgent
from src.integrations.exa import ExaClient
from src.integrations.google_drive import GoogleDriveClient
from src.db.session import init_db, get_db
from src.db.models import Session, WorkflowEvent, Persona, Sequence
from src.integrations.slack_blocks import (
    confirmation_card,
    clarification_card,
    persona_list_card,
    sequence_step_card,
    sequence_brief_card,
    edit_step_modal,
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
editor = SequenceEditorAgent()
exa = ExaClient()
drive = GoogleDriveClient()

REP_ROLES = {}  # slack user_id -> "AE" | "MDR"


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


# ---------------------------------------------------------------------------
# Phase 1 — Message intake + confirmation card
# ---------------------------------------------------------------------------

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

    request = RepRequest(
        raw_message=text,
        rep_id=user_id,
        rep_role=rep_role,
        channel_id=channel_id,
        timestamp=datetime.utcnow().isoformat(),
    )
    normalized = normalizer.normalize(request)

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


# ---------------------------------------------------------------------------
# Phase 2 — Persona discovery
# ---------------------------------------------------------------------------

@app.action("confirm_intent")
def handle_confirm_intent(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    log_event(session_id, "intent_confirmed", 1, user_id, {})

    say(text="Running persona discovery... give me a moment.", thread_ts=thread_ts)

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found. Please start a new request.", thread_ts=thread_ts)
            return

        # Store thread_ts so edit handlers can post back to the right thread
        session.thread_ts = thread_ts
        db.commit()

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
                text=(
                    f"I couldn't find any contacts at *{account_name}* in Apollo. "
                    "Check that the account name is correct or try a different company name."
                ),
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

        say(
            blocks=persona_list_card(personas, session_id),
            text=f"Found {len(personas)} personas at {account_name}. Select who to include:",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"Phase 2 failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong during persona discovery. Please try again.", thread_ts=thread_ts)


# Acknowledge persona checkbox interactions (state is read at confirm time)
@app.action(re.compile(r"approve_persona_.*"))
def handle_persona_checkbox(ack, body):
    ack()


# ---------------------------------------------------------------------------
# Phase 3 — Checkpoint 1: persona approval → scoring → sequence generation
# ---------------------------------------------------------------------------

@app.action("confirm_personas")
def handle_confirm_personas(ack, body, say):
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
        say(text="Please select at least one persona before confirming.", thread_ts=thread_ts)
        return

    try:
        db = next(get_db())

        # Mark approved / rejected
        all_personas = db.query(Persona).filter(Persona.session_id == session_id).all()
        for p in all_personas:
            p.approved_by_rep = p.id in selected_ids
            p.status = "approved" if p.id in selected_ids else "rejected"

        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.phase = 3
            # Keep thread_ts in sync if it was set here
            if not session.thread_ts:
                session.thread_ts = thread_ts
        db.commit()

        log_event(session_id, "personas_approved", 2, user_id, {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
        })

        say(
            text=f"Got it — {len(selected_ids)} persona(s) confirmed. Researching account and generating sequences...",
            thread_ts=thread_ts,
        )

        account_name = session.account_name if session else ""
        normalized = (session.normalized_request or {}) if session else {}
        account_domain = normalized.get("account_domain") or (session.account_domain if session else "")
        account_description = normalized.get("company_description")

        # --- Exa account research (best-effort) ---
        exa_signals = exa.research_account(account_name, account_domain or None)
        if exa_signals:
            # Attach signals to normalized_request for scorer context
            if session:
                nr = dict(session.normalized_request or {})
                nr["exa_signals"] = exa_signals
                session.normalized_request = nr
                db.commit()
            logger.info(f"[main] Exa returned {len(exa_signals)} signals for '{account_name}'")

        # --- Google Drive account plan (best-effort) ---
        account_plan_text = drive.find_account_plan(account_name)
        if account_plan_text and session:
            nr = dict(session.normalized_request or {})
            nr["account_plan_text"] = account_plan_text[:4000]
            session.normalized_request = nr
            db.commit()
            logger.info(f"[main] Drive account plan found for '{account_name}'")

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
                "gong_hook": p.gong_hook,
                "account_name": p.account_name,
            }
            for p in approved_personas
        ]

        # Score + value-map (passes exa_signals and account_plan_text for richer hooks)
        scored_personas = scorer.score(
            personas_dicts,
            account_description=account_description,
            exa_signals=exa_signals,
            account_plan_text=account_plan_text,
        )

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
                rep_name="Your Rep",
                session_id=session_id,
            )
            sequences.append(seq)

            seq_row = Sequence(
                id=seq["id"],
                session_id=session_id,
                persona_id=sp["id"],
                lane=seq["lane"],
                status="rep_review",
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

        # Post each sequence for rep review
        for seq in sequences:
            persona_match = next((p for p in scored_personas if p["id"] == seq["persona_id"]), {})
            name = f"{persona_match.get('first_name', '')} {persona_match.get('last_name', '')}".strip()
            lane = seq["lane"]

            all_blocks = [{
                "type": "header",
                "text": {"type": "plain_text", "text": f"Sequence: {name} ({lane} lane)"},
            }]
            for step in seq["steps"]:
                all_blocks.extend(sequence_step_card(step, name, seq["id"]))

            all_blocks.append({
                "type": "actions",
                "block_id": f"approve_sequence_{seq['id']}",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve All & Deliver Brief"},
                    "style": "primary",
                    "action_id": "approve_sequence",
                    "value": seq["id"],
                }],
            })

            say(blocks=all_blocks, text=f"Sequence for {name}", thread_ts=thread_ts)

    except Exception as e:
        logger.error(f"confirm_personas failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong saving your selections. Please try again.", thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Phase 4 — Checkpoint 2: step-level edit loop
# ---------------------------------------------------------------------------

@app.action(re.compile(r"edit_step_.*"))
def handle_edit_step(ack, body, client):
    """Rep clicks Edit on a step — open a modal to capture their instruction."""
    ack()
    value = body["actions"][0]["value"]  # "sequence_id:step_number"
    trigger_id = body["trigger_id"]

    try:
        sequence_id, step_number_str = value.split(":", 1)
        step_number = int(step_number_str)

        db = next(get_db())
        seq = db.query(Sequence).filter(Sequence.id == sequence_id).first()
        if not seq:
            return

        step = next((s for s in (seq.steps or []) if s["step_number"] == step_number), None)
        if not step:
            return

        # Get thread_ts from session so the modal submit can post back to the right thread
        session = db.query(Session).filter(Session.id == seq.session_id).first()
        thread_ts = (session.thread_ts or "") if session else ""

        client.views_open(
            trigger_id=trigger_id,
            view=edit_step_modal(step, sequence_id, thread_ts),
        )

    except Exception as e:
        logger.error(f"handle_edit_step failed: {e}", exc_info=True)


@app.view("edit_step_modal_submit")
def handle_edit_modal_submit(ack, body, client, say):
    """Rep submits the edit modal — apply the edit and re-post the updated step."""
    ack()
    user_id = body["user"]["id"]
    metadata = body["view"]["private_metadata"]  # "sequence_id:step_number:thread_ts"
    state = body["view"]["state"]["values"]

    try:
        parts = metadata.split(":", 2)
        sequence_id = parts[0]
        step_number = int(parts[1])
        thread_ts = parts[2] if len(parts) > 2 else ""

        instruction = state["edit_instruction"]["instruction_input"]["value"] or ""
        edit_field = "body"
        if "edit_field_select" in state:
            edit_field = state["edit_field_select"]["field_select"].get("selected_option", {}).get("value", "body")

        db = next(get_db())
        seq = db.query(Sequence).filter(Sequence.id == sequence_id).first()
        if not seq:
            return

        steps = list(seq.steps or [])
        step_idx = next((i for i, s in enumerate(steps) if s["step_number"] == step_number), None)
        if step_idx is None:
            return

        original_step = steps[step_idx]
        updated_step = editor.apply_edit(original_step, instruction, edit_field)
        updated_step["status"] = "draft"  # reset approval on edit

        # Record edit history
        history = list(seq.edit_history or [])
        history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "step_number": step_number,
            "instruction": instruction,
            "field": edit_field,
            "before_body": original_step.get("body"),
            "after_body": updated_step.get("body"),
        })

        steps[step_idx] = updated_step
        seq.steps = steps
        seq.edit_history = history
        db.commit()

        log_event(seq.session_id, "step_edited", 4, user_id, {
            "sequence_id": sequence_id,
            "step_number": step_number,
            "instruction": instruction,
        })

        # Get persona name for display
        persona = db.query(Persona).filter(Persona.id == seq.persona_id).first()
        persona_name = f"{persona.first_name} {persona.last_name}".strip() if persona else ""

        # Get channel_id from session
        session = db.query(Session).filter(Session.id == seq.session_id).first()
        channel_id = session.channel_id if session else None

        if channel_id and thread_ts:
            updated_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✏️ *Step {step_number} updated* — here's the revised version:",
                    },
                }
            ]
            updated_blocks.extend(sequence_step_card(updated_step, persona_name, sequence_id))

            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=updated_blocks,
                text=f"Step {step_number} updated.",
            )

    except Exception as e:
        logger.error(f"handle_edit_modal_submit failed: {e}", exc_info=True)


@app.action(re.compile(r"approve_step_.*"))
def handle_approve_step(ack, body, say):
    """Rep approves an individual step."""
    ack()
    value = body["actions"][0]["value"]  # "sequence_id:step_number"
    thread_ts = body["message"]["ts"]

    try:
        sequence_id, step_number_str = value.split(":", 1)
        step_number = int(step_number_str)

        db = next(get_db())
        seq = db.query(Sequence).filter(Sequence.id == sequence_id).first()
        if not seq:
            return

        steps = list(seq.steps or [])
        for s in steps:
            if s["step_number"] == step_number:
                s["status"] = "approved"
        seq.steps = steps
        db.commit()

        approved_count = sum(1 for s in steps if s.get("status") == "approved")
        say(
            text=f"✅ Step {step_number} approved. ({approved_count}/{len(steps)} steps done)",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"handle_approve_step failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Phase 5 — Checkpoint 2: full sequence approval → brief delivery
# ---------------------------------------------------------------------------

@app.action("approve_sequence")
def handle_approve_sequence(ack, body, say):
    """Rep approves the full sequence — delivers the final brief."""
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


# ---------------------------------------------------------------------------
# Utility handlers
# ---------------------------------------------------------------------------

@app.action("edit_intent")
def handle_edit_intent(ack, body, say):
    ack()
    say(
        text="No problem — tell me what to change. Which account did you want to target?",
        thread_ts=body["message"]["ts"],
    )


@app.action("submit_clarification")
def handle_submit_clarification(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    state = body.get("state", {}).get("values", {})
    clarification_text = ""
    for block_values in state.values():
        for action_values in block_values.values():
            clarification_text = action_values.get("value", "")

    log_event(session_id, "intent_corrected", 1, user_id, {"clarification": clarification_text})

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
