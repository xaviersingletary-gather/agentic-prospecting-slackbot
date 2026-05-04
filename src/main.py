import logging
import os
import re
import sys
import uuid
from datetime import datetime

print("STARTUP: main.py loading...", flush=True)

# Ensure project root is in sys.path so 'src' is importable regardless of how
# Railway (or any other host) invokes this file.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

print("STARTUP: sys.path configured", flush=True)

try:
    import sentry_sdk
    from slack_bolt import App
except Exception as _import_err:
    print(f"FATAL IMPORT ERROR: {_import_err}", flush=True)
    sys.exit(1)
try:
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from src.config import settings
    from src.agents.normalizer import InputNormalizerAgent, RepRequest
    from src.agents.discovery import PersonaDiscoveryAgent
    from src.agents.scorer import ScorerAgent
    from src.agents.generator import SequenceGeneratorAgent
    from src.agents.editor import SequenceEditorAgent
    from src.agents.researcher import CompanyResearchAgent
    from src.agents.contact_researcher import ContactResearchAgent
    from src.agents.sales_play import SalesPlayAgent
    from src.agents.theme_router import ThemeRouterAgent, THEMES as CONTENT_THEMES
    from src.integrations.google_drive import GoogleDriveClient
    from src.db.session import init_db, get_db, get_session, SessionLocal
    from src.db.models import Session, WorkflowEvent, Persona, Sequence, CompanyResearch, ContactResearch
    from sqlalchemy.orm.attributes import flag_modified
    from src.integrations.slack_blocks import (
        confirmation_card,
        edit_confirmation_card,
        research_progress_blocks,
        research_brief_card,
        sales_play_card,
        contact_list_card,
        resume_session_card,
        sequence_step_card,
        sequence_brief_card,
        all_sequences_approval_card,
        session_complete_card,
        edit_step_modal,
    )
    print("STARTUP: all imports OK", flush=True)
except Exception as _import_err:
    print(f"FATAL IMPORT ERROR (src): {_import_err}", flush=True)
    sys.exit(1)

# Sentry — must be initialized before anything else
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=1.0,
    )

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

print("STARTUP: initializing Slack App...", flush=True)
try:
    app = App(token=settings.SLACK_BOT_TOKEN)
except Exception as _e:
    print(f"FATAL: App() failed: {_e}", flush=True)
    sys.exit(1)

print("STARTUP: initializing agents...", flush=True)
try:
    normalizer = InputNormalizerAgent()
    discovery = PersonaDiscoveryAgent()
    scorer = ScorerAgent()
    generator = SequenceGeneratorAgent()
    editor = SequenceEditorAgent()
    researcher = CompanyResearchAgent()
    contact_researcher_agent = ContactResearchAgent()
    sales_play_agent = SalesPlayAgent()
    theme_router = ThemeRouterAgent()
    drive = GoogleDriveClient()
except Exception as _e:
    print(f"FATAL: agent init failed: {_e}", flush=True)
    sys.exit(1)

print("STARTUP: all agents ready", flush=True)

REP_ROLES = {}  # slack user_id -> "AE" | "MDR"


def get_rep_role(user_id: str) -> str:
    return REP_ROLES.get(user_id, "AE")


def log_event(session_id: str, event_type: str, phase: int, rep_id: str, payload: dict):
    try:
        with get_session() as db:
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
# Account Research Bot v1 — natural-language DM entry point + run_research action
#
# No slash commands. Any DM is treated as a research request: account name is
# extracted, a session is created, and the rep gets a 4-checkbox persona card.
# Clicking "Run Research" fires the V1 pipeline (HubSpot snapshot + Exa +
# OpenRouter findings + Apollo contacts).
# ---------------------------------------------------------------------------

from src.handlers.dm_research import handle_research_dm as _v1_handle_dm
from src.handlers.persona_select import handle_run_research_action as _v1_run_research_action


@app.action("run_research")
def _v1_action_run_research(ack, body, respond):
    _v1_run_research_action(payload=body, ack=ack, respond=respond)


@app.message()
def handle_message(message, say):
    _v1_handle_dm(message=message, say=say)


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

    # Disable the confirmation card immediately so the rep can't double-click
    try:
        client.chat_update(
            channel=channel_id,
            ts=thread_ts,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"_Running research on *{body.get('message', {}).get('text', 'account')}*..._"},
            }],
            text="Research started.",
        )
    except Exception:
        pass

    log_event(session_id, "intent_confirmed", 1, user_id, {})

    db = None
    try:
        db = SessionLocal()
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found. Please start a new request.", thread_ts=thread_ts)
            return

        session.thread_ts = thread_ts
        session.phase = 2
        session.phase_label = "research_in_progress"
        db.commit()

        account_name = session.account_name

        # ---------------------------------------------------------------
        # Phase 2 — Company research with live progress
        # ---------------------------------------------------------------
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

        research_data = researcher.research(
            account_name=account_name,
            account_domain=session.account_domain or None,
            progress_callback=update_progress,
        )

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

        # Replace progress card with research brief (no button — contacts load automatically)
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

        # ---------------------------------------------------------------
        # Phase 3 — Contact sourcing + AE game plan (auto-advances)
        # ---------------------------------------------------------------
        session.phase = 4
        session.phase_label = "contacts_sourcing"
        db.commit()

        # Post a status placeholder — will be replaced with the AE game plan card
        status_resp = say(
            text=f"Sourcing contacts and building AE game plan for {account_name}...",
            thread_ts=thread_ts,
        )
        status_ts = status_resp.get("ts") if status_resp else None

        normalized = session.normalized_request or {}
        contacts = discovery.discover(
            session_id=session_id,
            account_name=account_name,
            account_domain=session.account_domain or None,
            persona_filter=normalized.get("persona_filter"),
        )

        if not contacts:
            no_contacts_blocks = [{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"I couldn't find any contacts at *{account_name}* in Apollo. Check the account name or try again.",
                },
            }]
            if status_ts:
                client.chat_update(
                    channel=channel_id,
                    ts=status_ts,
                    blocks=no_contacts_blocks,
                    text="No contacts found.",
                )
            else:
                say(blocks=no_contacts_blocks, text="No contacts found.", thread_ts=thread_ts)
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

        # Generate AE game plan — update the status placeholder with the result
        sales_play = sales_play_agent.generate(
            research_data=research_data,
            contacts=contacts,
            account_name=account_name,
        )
        play_blocks = sales_play_card(sales_play, account_name)
        if status_ts:
            try:
                client.chat_update(
                    channel=channel_id,
                    ts=status_ts,
                    blocks=play_blocks,
                    text=f"AE game plan for {account_name}",
                )
            except Exception as e:
                logger.warning(f"[sales_play] Failed to update status message: {e}")
                say(blocks=play_blocks, text=f"AE game plan for {account_name}", thread_ts=thread_ts)
        else:
            say(blocks=play_blocks, text=f"AE game plan for {account_name}", thread_ts=thread_ts)

        # Post contact list as a separate message after the game plan
        contact_blocks = contact_list_card(contacts, session_id, flagged_ids=set())
        say(
            blocks=contact_blocks,
            text=f"Found {len(contacts)} contacts at {account_name}.",
            thread_ts=thread_ts,
        )

    except Exception as e:
        logger.error(f"Phase 2-3 failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong during research. Please try again.", thread_ts=thread_ts)
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# find_contacts — kept for backward compat with sessions started before v2
# ---------------------------------------------------------------------------

@app.action("find_contacts")
def handle_find_contacts(ack, body, say):
    ack()
    say(
        text="Contact sourcing now runs automatically. If this session is stuck, cancel and start a new one.",
        thread_ts=body["message"]["ts"],
    )


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

    db = None
    try:
        session_id, persona_id = value.split(":", 1)
        db = SessionLocal()
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
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Phase 4–5 — Approve contacts → individual research + scoring + generation
# ---------------------------------------------------------------------------

@app.action("approve_contacts")
def handle_approve_contacts(ack, body, say, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    db = None
    try:
        db = SessionLocal()
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
                "company_priorities": cr_db.company_priorities or [],
                "raw_research_text": cr_db.raw_research_text or "",
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
        # Theme routing — select content theme based on account research signals
        # ------------------------------------------------------------------
        routing_result = theme_router.route(
            research_data=company_research or {},
            approved_personas=scored_personas,
        )
        normalized["theme_routing"] = {
            "primary_theme_id": routing_result["primary_theme_id"],
            "selection_rationale": routing_result["selection_rationale"],
            "method": routing_result["method"],
        }
        session.normalized_request = normalized
        flag_modified(session, "normalized_request")
        db.commit()

        log_event(session_id, "theme_selected", 5, user_id, {
            "primary_theme": routing_result["primary_theme_id"],
            "method": routing_result["method"],
        })

        theme_assignments = {
            a["persona_id"]: a
            for a in routing_result.get("persona_assignments", [])
        }

        # ------------------------------------------------------------------
        # Sequence generation — only for flagged contacts; fall back to all if none flagged
        # ------------------------------------------------------------------
        flagged_persona_ids = {p.id for p in all_personas if p.deep_research_flagged}
        sequence_targets = (
            [sp for sp in scored_personas if sp["id"] in flagged_persona_ids]
            if flagged_persona_ids
            else scored_personas
        )

        say(text=f"Generating sequences for {len(sequence_targets)} contact(s)...", thread_ts=work_thread)

        sequences = []
        for sp in sequence_targets:
            cr = contact_research_map.get(sp["id"])
            seq = generator.generate(
                persona=sp,
                account_name=session.account_name,
                account_description=account_description,
                rep_name="Your Rep",
                session_id=session_id,
                company_research=company_research,
                contact_research=cr,
                theme_assignment=theme_assignments.get(sp["id"]),
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

        session.phase = 6
        session.phase_label = "sequences_draft"
        db.commit()

        log_event(session_id, "sequences_generated", 5, user_id, {"count": len(sequences)})

        # Post each sequence for review
        for seq in sequences:
            match = next((p for p in sequence_targets if p["id"] == seq["persona_id"]), {})
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

            say(blocks=all_blocks, text=f"Sequence for {name}", thread_ts=work_thread)

        # Single collective approval card after all sequences are posted
        persona_names = [
            f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            for p in sequence_targets
        ]
        primary_theme = CONTENT_THEMES.get(routing_result["primary_theme_id"], {})
        say(
            blocks=all_sequences_approval_card(
                session_id,
                persona_names,
                theme_info={
                    "name": primary_theme.get("display_name", ""),
                    "rationale": routing_result["selection_rationale"],
                },
            ),
            text="Review sequences above, then approve all when ready.",
            thread_ts=work_thread,
        )

    except Exception as e:
        logger.error(f"approve_contacts failed for session {session_id}: {e}", exc_info=True)
        say(text="Something went wrong. Please try again.", thread_ts=thread_ts)
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Sequence edit loop
# ---------------------------------------------------------------------------

@app.action(re.compile(r"edit_step_.*"))
def handle_edit_step(ack, body, client):
    ack()
    value = body["actions"][0]["value"]
    trigger_id = body["trigger_id"]

    db = None
    try:
        sequence_id, step_number_str = value.split(":", 1)
        step_number = int(step_number_str)

        db = SessionLocal()
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
    finally:
        if db:
            db.close()


@app.view("edit_step_modal_submit")
def handle_edit_modal_submit(ack, body, client, say):
    ack()
    user_id = body["user"]["id"]
    metadata = body["view"]["private_metadata"]
    state = body["view"]["state"]["values"]

    db = None
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

        db = SessionLocal()
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
    finally:
        if db:
            db.close()


@app.action(re.compile(r"approve_step_.*"))
def handle_approve_step(ack, body, say):
    ack()
    value = body["actions"][0]["value"]
    thread_ts = body["message"]["ts"]

    db = None
    try:
        sequence_id, step_number_str = value.split(":", 1)
        step_number = int(step_number_str)

        db = SessionLocal()
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
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Sequence approval → brief delivery
# ---------------------------------------------------------------------------

@app.action("approve_sequence")
def handle_approve_sequence(ack, body, say):
    ack()
    sequence_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    db = None
    try:
        db = SessionLocal()
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
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Collective sequence approval → brief delivery + session complete
# ---------------------------------------------------------------------------

@app.action("approve_all_sequences")
def handle_approve_all_sequences(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    thread_ts = body["message"]["ts"]

    db = None
    try:
        db = SessionLocal()
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            say(text="Session not found.", thread_ts=thread_ts)
            return

        work_thread = session.thread_ts or thread_ts
        sequences = db.query(Sequence).filter(Sequence.session_id == session_id).all()

        persona_names = []
        for seq in sequences:
            seq.status = "approved"
            persona = db.query(Persona).filter(Persona.id == seq.persona_id).first()
            if persona:
                persona_dict = {
                    "first_name": persona.first_name,
                    "last_name": persona.last_name,
                    "title": persona.title,
                    "account_name": persona.account_name,
                }
                say(
                    blocks=sequence_brief_card({"lane": seq.lane, "steps": seq.steps or []}, persona_dict),
                    text=f"Brief for {persona.first_name} {persona.last_name} — ready to paste.",
                    thread_ts=work_thread,
                )
                persona_names.append(f"{persona.first_name} {persona.last_name}".strip())
                log_event(session_id, "sequence_approved", 6, user_id, {"sequence_id": seq.id})

        session.phase = 7
        session.phase_label = "completed"
        session.status = "completed"
        db.commit()

        say(
            blocks=session_complete_card(session.account_name, len(sequences), persona_names),
            text=f"Done — {len(sequences)} sequence(s) ready for {session.account_name}.",
            thread_ts=work_thread,
        )

    except Exception as e:
        logger.error(f"handle_approve_all_sequences failed for {session_id}: {e}", exc_info=True)
        say(text="Something went wrong delivering briefs.", thread_ts=thread_ts)
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Clear thread — type "clear" in any thread to delete all bot messages in it
# ---------------------------------------------------------------------------

@app.message(re.compile(r"^\s*clear\s*$", re.IGNORECASE))
def handle_clear(message, client, say):
    channel_id = message.get("channel")
    thread_ts = message.get("thread_ts")
    user_message_ts = message.get("ts")

    if not thread_ts:
        say(
            text="_Type `clear` inside a research thread to delete all bot messages in it._",
            thread_ts=user_message_ts,
        )
        return

    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts, limit=200)
        messages = result.get("messages", [])
        for msg in messages:
            if msg.get("bot_id"):
                try:
                    client.chat_delete(channel=channel_id, ts=msg["ts"])
                except Exception as e:
                    logger.warning(f"[clear] Could not delete message {msg.get('ts')}: {e}")
    except Exception as e:
        logger.error(f"[clear] Failed to fetch thread: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@app.action("resume_session")
def handle_resume_session(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]

    db = None
    try:
        db = SessionLocal()
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
    finally:
        if db:
            db.close()


@app.action("cancel_session")
def handle_cancel_session(ack, body, say):
    ack()
    session_id = body["actions"][0]["value"]

    db = None
    try:
        db = SessionLocal()
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.status = "cancelled"
            db.commit()
        say(text="Thread cancelled. Start a new account anytime by sending a message.")
    except Exception as e:
        logger.error(f"handle_cancel_session failed: {e}", exc_info=True)
    finally:
        if db:
            db.close()


# ---------------------------------------------------------------------------
# Utility handlers
# ---------------------------------------------------------------------------

@app.action("edit_intent")
def handle_edit_intent(ack, body, client):
    ack()
    session_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]
    message_ts = body["message"]["ts"]

    db = None
    try:
        db = SessionLocal()
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            return
        normalized = session.normalized_request or {}
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=edit_confirmation_card(
                account_name=normalized.get("account_name", ""),
                persona_filter=normalized.get("persona_filter"),
                use_case_angle=normalized.get("use_case_angle"),
                session_id=session_id,
            ),
            text="Edit your request:",
        )
    except Exception as e:
        logger.error(f"handle_edit_intent failed: {e}", exc_info=True)
    finally:
        if db:
            db.close()


@app.action("submit_edit")
def handle_submit_edit(ack, body, client):
    ack()
    session_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]
    message_ts = body["message"]["ts"]
    state = body.get("state", {}).get("values", {})

    account_name = ""
    persona_filter = None
    use_case_angle = None

    for block_id, block_values in state.items():
        if block_id.startswith("edit_account_"):
            account_name = (block_values.get("edit_account_input", {}).get("value") or "").strip()
        elif block_id.startswith("edit_personas_"):
            raw = (block_values.get("edit_personas_input", {}).get("value") or "").strip()
            persona_filter = [p.strip() for p in raw.split(",") if p.strip()] or None
        elif block_id.startswith("edit_angle_"):
            val = (block_values.get("edit_angle_input", {}).get("value") or "").strip()
            use_case_angle = val or None

    db = None
    try:
        db = SessionLocal()
        session = db.query(Session).filter(Session.id == session_id).first()
        if not session:
            return

        normalized = dict(session.normalized_request or {})
        normalized["account_name"] = account_name
        normalized["persona_filter"] = persona_filter
        normalized["use_case_angle"] = use_case_angle
        session.account_name = account_name
        session.normalized_request = normalized
        flag_modified(session, "normalized_request")
        db.commit()

        log_event(session_id, "intent_edited", 1, user_id, {
            "account_name": account_name,
            "persona_filter": persona_filter,
            "use_case_angle": use_case_angle,
        })

        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=confirmation_card(account_name, persona_filter, use_case_angle, session_id),
            text=f"Got it. Here's what I'll run for {account_name}.",
        )
    except Exception as e:
        logger.error(f"handle_submit_edit failed: {e}", exc_info=True)
    finally:
        if db:
            db.close()


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
    print("STARTUP: __main__ block entered", flush=True)
    import sys as _sys
    import threading as _threading
    if settings.DATABASE_URL:
        _db_error = []

        def _run_init_db():
            try:
                init_db()
            except Exception as _e:
                _db_error.append(_e)

        print("STARTUP: calling init_db() (15s timeout)...", flush=True)
        _t = _threading.Thread(target=_run_init_db, daemon=True)
        _t.start()
        _t.join(timeout=15)
        if _t.is_alive():
            print("WARNING: init_db() timed out after 15s — DB may not be fully initialized", flush=True)
            logger.warning("init_db() timed out — continuing without full DB initialization")
        elif _db_error:
            print(f"WARNING: Database init error (non-fatal): {_db_error[0]}", flush=True)
            logger.warning(f"init_db() failed (non-fatal): {_db_error[0]}")
        else:
            print("STARTUP: init_db() OK", flush=True)
            logger.info("Database initialized")
    else:
        logger.warning("DATABASE_URL not set — skipping DB initialization")

    print("STARTUP: creating SocketModeHandler...", flush=True)
    logger.info(f"Starting Gather AI Prospecting Bot [{settings.ENVIRONMENT}]")
    try:
        handler = SocketModeHandler(app, settings.SLACK_APP_TOKEN)
        handler.start()
    except Exception as _e:
        logger.error(f"SocketModeHandler FAILED: {_e}", exc_info=True)
        _sys.stderr.write(f"FATAL: Slack handler failed: {_e}\n")
        _sys.stderr.flush()
        _sys.exit(1)
