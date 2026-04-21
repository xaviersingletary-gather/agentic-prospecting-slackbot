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
from src.agents.researcher import CompanyResearchAgent
from src.agents.contact_researcher import ContactResearchAgent
from src.integrations.google_drive import GoogleDriveClient
from src.db.session import init_db, get_db
from src.db.models import Session, WorkflowEvent, Persona, Sequence, CompanyResearch, ContactResearch
from src.integrations.slack_blocks import (
    confirmation_card,
    clarification_card,
    research_progress_blocks,
    research_brief_card,
    contact_list_card,
    resume_session_card,
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
researcher = CompanyResearchAgent()
contact_researcher_agent = ContactResearchAgent()
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
# Phase 1 — Message intake + confirmation card (or session resume prompt)
# ---------------------------------------------------------------------------

@app.message()
def handle_message(message, say, client):
    user_id = message.get("user")
    channel_id = message.get("channel")
    text = message.get("text", "").strip()

    if not text or message.get("bot_id"):
        return

    # Check for an existing active session for this rep
    try:
        db = next(get_db())
        existing = (
            db.query(Session)
            .filter(Session.rep_id == user_id, Session.status == "active")
            .order_by(Session.created_at.desc())
            .first()
        )
        if existing:
            say(
                blocks=resume_session_card(existing),
                text=(
                    f"You have an active session for {existing.account_name}. "
                    "Continue or cancel?"
                ),
            )
            return
    except Exception as e:
        logger.error(f"Session resume check failed: {e}")

    # No active session — start a new one
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
            phase_label="confirmation",
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
            text=f"Got it — researching {normalized.account_name}. Is that right?",
        )


# ---------------------------------------------------------------------------
# Phase 2 — Company Research (triggered by "Yes, run it" button)
# ---------------------------------------------------------------------------

@app.action("confirm_intent")
def handle_confirm_intent(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]
    channel_id = body["channel"]["id"]

    log_event(session_id, "intent_confirmed", 1, user_id, {})

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found. Please start a new request.", thread_ts=thread_ts)
            return

        session.thread_ts = thread_ts
        session.phase = 2
        session.phase_label = "research_in_progress"
        db.commit()

        account_name = session.account_name

        # Post initial progress message — updated in place as each step completes
        progress_resp = say(
            blocks=research_progress_blocks(account_name, ["⏳ Starting company research..."]),
            text=f"Researching {account_name}...",
            thread_ts=thread_ts,
        )
        progress_ts = progress_resp.get("ts") if progress_resp else None

        if progress_ts:
            session.progress_message_ts = progress_ts
            db.commit()

        completed_steps = []

        def update_progress(step_text: str):
            completed_steps.append(step_text)
            if progress_ts:
                try:
                    client.chat_update(
                        channel=channel_id,
                        ts=progress_ts,
                        blocks=research_progress_blocks(account_name, completed_steps),
                        text=f"Researching {account_name}...",
                    )
                except Exception as e:
                    logger.warning(f"Progress update failed: {e}")

        # Run company research with live progress
        research_data = researcher.research(
            account_name=account_name,
            account_domain=session.account_domain or None,
            progress_callback=update_progress,
        )

        # Persist CompanyResearch to DB
        cr_row = CompanyResearch(
            id=research_data["id"],
            session_id=session_id,
            account_name=research_data["account_name"],
            is_public_company=research_data.get("is_public_company"),
            facility_count=research_data.get("facility_count"),
            facility_count_note=research_data.get("facility_count_note"),
            total_sqft_estimate=research_data.get("total_sqft_estimate"),
            sqft_source=research_data.get("sqft_source"),
            board_initiatives=research_data.get("board_initiatives", []),
            company_priorities=research_data.get("company_priorities", []),
            trigger_events=research_data.get("trigger_events", []),
            automation_vendors=research_data.get("automation_vendors", []),
            exception_tax=research_data.get("exception_tax"),
            research_gaps=research_data.get("research_gaps", []),
            documents_used=research_data.get("documents_used", []),
            raw_research_text=research_data.get("raw_research_text", ""),
        )
        db.add(cr_row)

        session.phase = 3
        session.phase_label = "research_complete"
        db.commit()

        log_event(session_id, "research_complete", 2, user_id, {
            "facility_count": research_data.get("facility_count"),
            "initiative_count": len(research_data.get("board_initiatives", [])),
            "gap_count": len(research_data.get("research_gaps", [])),
        })

        # Replace progress message with full research brief
        brief_blocks = research_brief_card(research_data, session_id)
        if progress_ts:
            client.chat_update(
                channel=channel_id,
                ts=progress_ts,
                blocks=brief_blocks,
                text=f"Research brief ready for {account_name}",
            )
        else:
            say(blocks=brief_blocks, text=f"Research brief for {account_name}", thread_ts=thread_ts)

    except Exception as e:
        logger.error(f"Phase 2 research failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong during research. Please try again.", thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Phase 3 — Contact Sourcing (triggered by "Find contacts" button)
# ---------------------------------------------------------------------------

@app.action("find_contacts")
def handle_find_contacts(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    # Container message ts is the research brief message; thread_ts is the thread root
    thread_ts = body.get("container", {}).get("message_ts") or body["message"]["ts"]

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found.", thread_ts=thread_ts)
            return

        work_thread = session.thread_ts or thread_ts
        session.phase = 4
        session.phase_label = "contacts_sourcing"
        db.commit()

        say(text="Pulling contacts from Apollo...", thread_ts=work_thread)

        normalized = session.normalized_request or {}
        contacts = discovery.discover(
            session_id=session_id,
            account_name=session.account_name,
            persona_filter=normalized.get("persona_filter"),
        )

        if not contacts:
            say(
                text=(
                    f"I couldn't find any contacts at *{session.account_name}* in Apollo. "
                    "Check the account name or try a different company."
                ),
                thread_ts=work_thread,
            )
            log_event(session_id, "discovery_empty", 3, user_id, {})
            return

        for c in contacts:
            row = Persona(
                id=c["id"],
                session_id=session_id,
                apollo_id=c.get("apollo_id"),
                first_name=c["first_name"],
                last_name=c["last_name"],
                title=c["title"],
                email=c.get("email"),
                linkedin_url=c.get("linkedin_url"),
                account_name=c["account_name"],
                persona_type=c["persona_type"],
                seniority=c["seniority"],
                outreach_lane=c["outreach_lane"],
                priority_score=c["priority_score"],
                linkedin_signals=c.get("linkedin_signals", []),
                deep_research_flagged=False,
                status="discovered",
            )
            db.merge(row)

        session.phase_label = "contacts_sourced"
        db.commit()

        log_event(session_id, "contacts_sourced", 3, user_id, {"contact_count": len(contacts)})

        say(
            blocks=contact_list_card(contacts, session_id, flagged_ids=set()),
            text=f"Found {len(contacts)} contacts at {session.account_name}.",
            thread_ts=work_thread,
        )

    except Exception as e:
        logger.error(f"handle_find_contacts failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong pulling contacts. Please try again.", thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Contact flag toggle (⭐ button per contact row)
# ---------------------------------------------------------------------------

@app.action(re.compile(r"flag_contact_.*"))
def handle_flag_contact(ack, body, client):
    ack()
    value = body["actions"][0]["value"]  # "session_id:persona_id"
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    message_ts = body["message"]["ts"]

    try:
        session_id, persona_id = value.split(":", 1)
        db = next(get_db())

        persona = db.query(Persona).filter(Persona.id == persona_id).first()
        if not persona:
            return

        # Count currently flagged contacts (excluding this one)
        flagged_count = (
            db.query(Persona)
            .filter(
                Persona.session_id == session_id,
                Persona.deep_research_flagged == True,
                Persona.id != persona_id,
            )
            .count()
        )

        # Enforce cap of 3
        if not persona.deep_research_flagged and flagged_count >= 3:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="You've flagged 3 contacts — that's the max. Remove a flag before adding another.",
                thread_ts=message_ts,
            )
            return

        persona.deep_research_flagged = not persona.deep_research_flagged
        db.commit()

        # Re-render the contact list with updated flags
        all_contacts = (
            db.query(Persona)
            .filter(Persona.session_id == session_id, Persona.status == "discovered")
            .all()
        )
        contacts_dicts = [
            {
                "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
                "title": p.title, "persona_type": p.persona_type,
                "outreach_lane": p.outreach_lane, "priority_score": p.priority_score,
                "account_name": p.account_name,
            }
            for p in all_contacts
        ]
        flagged_ids = {p.id for p in all_contacts if p.deep_research_flagged}

        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=contact_list_card(contacts_dicts, session_id, flagged_ids=flagged_ids),
            text=f"Contact list updated — {len(flagged_ids)}/3 flagged for deep research.",
        )

    except Exception as e:
        logger.error(f"handle_flag_contact failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Phase 4–5 — Approve contacts → individual research + scoring + generation
# ---------------------------------------------------------------------------

@app.action("approve_contacts")
def handle_approve_contacts(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found.", thread_ts=thread_ts)
            return

        work_thread = session.thread_ts or thread_ts

        all_personas = (
            db.query(Persona)
            .filter(Persona.session_id == session_id, Persona.status == "discovered")
            .all()
        )

        if not all_personas:
            say(text="No contacts found. Please start over.", thread_ts=work_thread)
            return

        for p in all_personas:
            p.status = "approved"
            p.approved_by_rep = True
        db.commit()

        flagged_contacts = [p for p in all_personas if p.deep_research_flagged]
        flagged_dicts = [
            {
                "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
                "title": p.title, "account_name": p.account_name, "session_id": session_id,
            }
            for p in flagged_contacts
        ]

        session.phase = 5
        session.phase_label = "individual_research"
        db.commit()

        log_event(session_id, "contacts_approved", 4, user_id, {
            "total": len(all_personas),
            "flagged": len(flagged_contacts),
        })

        # ------------------------------------------------------------------
        # Individual research for flagged contacts (parallel)
        # ------------------------------------------------------------------
        contact_research_map = {}
        if flagged_dicts:
            say(
                text=f"Running deep research on {len(flagged_dicts)} flagged contact(s)...",
                thread_ts=work_thread,
            )
            raw_contact_research = contact_researcher_agent.research_contacts(
                contacts=flagged_dicts,
            )
            for persona_id, cr_data in raw_contact_research.items():
                cr_row = ContactResearch(
                    id=cr_data["id"],
                    persona_id=persona_id,
                    session_id=session_id,
                    current_role_tenure=cr_data.get("current_role_tenure"),
                    prior_roles=cr_data.get("prior_roles", []),
                    recent_linkedin=cr_data.get("recent_linkedin", []),
                    speaking_activity=cr_data.get("speaking_activity"),
                    research_gaps=cr_data.get("research_gaps", []),
                )
                db.add(cr_row)
                contact_research_map[persona_id] = cr_data
            db.commit()

        # ------------------------------------------------------------------
        # Load company research for sequence context
        # ------------------------------------------------------------------
        cr_db = (
            db.query(CompanyResearch)
            .filter(CompanyResearch.session_id == session_id)
            .order_by(CompanyResearch.created_at.desc())
            .first()
        )
        company_research = None
        if cr_db:
            company_research = {
                "account_name": cr_db.account_name,
                "facility_count": cr_db.facility_count,
                "board_initiatives": cr_db.board_initiatives or [],
                "trigger_events": cr_db.trigger_events or [],
                "exception_tax": cr_db.exception_tax,
            }

        # ------------------------------------------------------------------
        # Scoring
        # ------------------------------------------------------------------
        normalized = session.normalized_request or {}
        account_description = normalized.get("company_description")

        account_plan_text = None
        try:
            account_plan_text = drive.find_account_plan(session.account_name)
        except Exception:
            pass

        personas_dicts = [
            {
                "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
                "title": p.title, "seniority": p.seniority, "persona_type": p.persona_type,
                "outreach_lane": p.outreach_lane, "priority_score": p.priority_score,
                "linkedin_signals": p.linkedin_signals or [], "gong_hook": p.gong_hook,
                "account_name": p.account_name,
            }
            for p in all_personas
        ]

        scored_personas = scorer.score(
            personas_dicts,
            account_description=account_description,
            account_plan_text=account_plan_text,
        )

        for sp in scored_personas:
            db_p = db.query(Persona).filter(Persona.id == sp["id"]).first()
            if db_p:
                db_p.priority_score = sp["priority_score"]
                db_p.score_reasoning = sp.get("score_reasoning")
                db_p.value_driver = sp.get("value_driver")
        db.commit()

        # ------------------------------------------------------------------
        # Sequence generation
        # ------------------------------------------------------------------
        say(text="Generating sequences...", thread_ts=work_thread)

        sequences = []
        for sp in scored_personas:
            cr = contact_research_map.get(sp["id"])
            seq = generator.generate(
                persona=sp,
                account_name=session.account_name,
                account_description=account_description,
                rep_name="Your Rep",
                session_id=session_id,
                company_research=company_research,
                contact_research=cr,
            )
            sequences.append(seq)

            seq_row = Sequence(
                id=seq["id"],
                session_id=session_id,
                persona_id=sp["id"],
                lane=seq["lane"],
                personalization_tier=seq.get("personalization_tier", "standard"),
                status="rep_review",
                steps=seq["steps"],
                edit_history=[],
            )
            db.add(seq_row)

        session.phase = 6
        session.phase_label = "sequences_draft"
        db.commit()

        log_event(session_id, "sequences_generated", 5, user_id, {"count": len(sequences)})

        # Post each sequence for review
        for seq in sequences:
            match = next((p for p in scored_personas if p["id"] == seq["persona_id"]), {})
            name = f"{match.get('first_name', '')} {match.get('last_name', '')}".strip()
            lane = seq["lane"]
            tier_badge = " ⭐" if seq.get("personalization_tier") == "deep" else ""

            all_blocks = [{
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Sequence: {name} ({lane} lane{tier_badge})",
                },
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

            say(blocks=all_blocks, text=f"Sequence for {name}", thread_ts=work_thread)

    except Exception as e:
        logger.error(f"approve_contacts failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong. Please try again.", thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Sequence edit loop
# ---------------------------------------------------------------------------

@app.action(re.compile(r"edit_step_.*"))
def handle_edit_step(ack, body, client):
    ack()
    value = body["actions"][0]["value"]
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
    ack()
    user_id = body["user"]["id"]
    metadata = body["view"]["private_metadata"]
    state = body["view"]["state"]["values"]

    try:
        parts = metadata.split(":", 2)
        sequence_id = parts[0]
        step_number = int(parts[1])
        thread_ts = parts[2] if len(parts) > 2 else ""

        instruction = state["edit_instruction"]["instruction_input"]["value"] or ""
        edit_field = "body"
        if "edit_field_select" in state:
            edit_field = (
                state["edit_field_select"]["field_select"]
                .get("selected_option", {})
                .get("value", "body")
            )

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
        updated_step["status"] = "draft"

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

        log_event(seq.session_id, "step_edited", 6, user_id, {
            "sequence_id": sequence_id,
            "step_number": step_number,
        })

        persona = db.query(Persona).filter(Persona.id == seq.persona_id).first()
        persona_name = f"{persona.first_name} {persona.last_name}".strip() if persona else ""

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
    ack()
    value = body["actions"][0]["value"]
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
# Sequence approval → brief delivery
# ---------------------------------------------------------------------------

@app.action("approve_sequence")
def handle_approve_sequence(ack, body, say):
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
            session.phase = 7
            session.phase_label = "sequences_approved"

        persona = db.query(Persona).filter(Persona.id == seq.persona_id).first()
        db.commit()

        log_event(seq.session_id, "sequence_approved", 6, user_id, {"sequence_id": sequence_id})

        if not persona:
            say(text="Sequence approved, but persona record not found.", thread_ts=thread_ts)
            return

        persona_dict = {
            "first_name": persona.first_name,
            "last_name": persona.last_name,
            "title": persona.title,
            "account_name": persona.account_name,
        }
        sequence_dict = {"lane": seq.lane, "steps": seq.steps or []}

        say(
            blocks=sequence_brief_card(sequence_dict, persona_dict),
            text=f"Sequence brief for {persona.first_name} {persona.last_name} — ready to paste into Apollo.",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"approve_sequence failed for {sequence_id}: {e}", exc_info=True)
        say(text="Something went wrong delivering the brief.", thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@app.action("resume_session")
def handle_resume_session(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found. Please start a new request.")
            return

        phase = session.phase

        if phase <= 3:
            cr_db = (
                db.query(CompanyResearch)
                .filter(CompanyResearch.session_id == session_id)
                .order_by(CompanyResearch.created_at.desc())
                .first()
            )
            if cr_db:
                research_data = {
                    "account_name": cr_db.account_name,
                    "facility_count": cr_db.facility_count,
                    "facility_count_note": cr_db.facility_count_note,
                    "board_initiatives": cr_db.board_initiatives or [],
                    "company_priorities": cr_db.company_priorities or [],
                    "trigger_events": cr_db.trigger_events or [],
                    "automation_vendors": cr_db.automation_vendors or [],
                    "exception_tax": cr_db.exception_tax,
                    "research_gaps": cr_db.research_gaps or [],
                }
                say(
                    blocks=research_brief_card(research_data, session_id),
                    text=f"Resuming {session.account_name} — here's the research brief.",
                )
            else:
                say(text=f"Resuming *{session.account_name}*. Research is in progress.")

        elif phase == 4:
            contacts = (
                db.query(Persona)
                .filter(Persona.session_id == session_id, Persona.status == "discovered")
                .all()
            )
            contacts_dicts = [
                {
                    "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
                    "title": p.title, "persona_type": p.persona_type,
                    "outreach_lane": p.outreach_lane, "priority_score": p.priority_score,
                    "account_name": p.account_name,
                }
                for p in contacts
            ]
            flagged_ids = {p.id for p in contacts if p.deep_research_flagged}
            say(
                blocks=contact_list_card(contacts_dicts, session_id, flagged_ids=flagged_ids),
                text=f"Resuming *{session.account_name}* — here's your contact list.",
            )

        else:
            say(
                text=(
                    f"Resuming *{session.account_name}*. "
                    "Sequences are generated — scroll up in the thread to review, "
                    "or tell me what you'd like to change."
                ),
                thread_ts=session.thread_ts,
            )

    except Exception as e:
        logger.error(f"handle_resume_session failed: {e}", exc_info=True)
        say(text="Something went wrong resuming your session.")


@app.action("cancel_session")
def handle_cancel_session(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]

    try:
        db = next(get_db())
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.status = "cancelled"
            db.commit()
        say(text="Session cancelled. Send me a new account name to start fresh.")
    except Exception as e:
        logger.error(f"handle_cancel_session failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Utility handlers
# ---------------------------------------------------------------------------

@app.action("edit_intent")
def handle_edit_intent(ack, body, say):
    ack()
    say(
        text="No problem — which account did you want to target?",
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


# Acknowledge any leftover persona checkbox interactions from old sessions
@app.action(re.compile(r"approve_persona_.*"))
def handle_persona_checkbox(ack, body):
    ack()


if __name__ == "__main__":
    if settings.DATABASE_URL:
        init_db()
        logger.info("Database initialized")
    else:
        logger.warning("DATABASE_URL not set — skipping DB initialization")

    logger.info(f"Starting Gather AI Prospecting Bot [{settings.ENVIRONMENT}]")
    handler = SocketModeHandler(app, settings.SLACK_APP_TOKEN)
    handler.start()
