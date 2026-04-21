from typing import Optional

# ---------------------------------------------------------------------------
# Research progress blocks — updated in place via chat.update
# ---------------------------------------------------------------------------

def research_progress_blocks(account_name: str, steps: list) -> list:
    """
    Live-updating progress card for company research.
    `steps` is an ordered list of status lines added as each step completes.
    """
    steps_text = "\n".join(steps) if steps else "⏳ Starting..."
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Researching {account_name}...*\n\n{steps_text}",
            },
        }
    ]


# ---------------------------------------------------------------------------
# Research brief card — Checkpoint 1
# ---------------------------------------------------------------------------

def research_brief_card(research: dict, session_id: str) -> list:
    """
    Full research brief posted after company research completes.
    Includes a 'Find contacts' button to advance to Checkpoint 2.
    """
    account_name = research.get("account_name", "")
    facility_count = research.get("facility_count")
    facility_note = research.get("facility_count_note") or ""
    board_initiatives = research.get("board_initiatives") or []
    company_priorities = research.get("company_priorities") or []
    trigger_events = research.get("trigger_events") or []
    automation_vendors = research.get("automation_vendors") or []
    exception_tax = research.get("exception_tax") or {}
    research_gaps = research.get("research_gaps") or []

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Research Brief — {account_name}"},
        },
    ]

    # --- Facilities ---
    if facility_count:
        fac_str = f"~{facility_count:,} DCs/facilities"
        if facility_note:
            fac_str += f" _({facility_note})_"
    else:
        fac_str = "_Facility count not found — see Research Gaps_"
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Facilities:* {fac_str}"},
    })

    # --- Board initiatives ---
    if board_initiatives:
        init_lines = []
        for i, item in enumerate(board_initiatives[:3], 1):
            source = f" _(per {item.get('source', '')})_" if item.get("source") else ""
            init_lines.append(
                f"{i}. *{item.get('title', '')}* — {item.get('summary', '')}{source}"
            )
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Board / Exec Initiatives*\n" + "\n".join(init_lines),
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Board / Exec Initiatives*\n_None found with clear sources._",
            },
        })

    # --- Company priorities ---
    if company_priorities:
        prio_text = "\n".join(f"• {p}" for p in company_priorities[:2])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Operational Priorities*\n{prio_text}"},
        })

    # --- Trigger events ---
    if trigger_events:
        trigger_lines = []
        for t in trigger_events[:2]:
            date_str = f" ({t.get('date', '')})" if t.get("date") else ""
            src_str = f" — _{t.get('source', '')}_" if t.get("source") else ""
            trigger_lines.append(
                f"• {t.get('description', '')}{date_str}{src_str}"
            )
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Trigger Events*\n" + "\n".join(trigger_lines),
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Trigger Events*\n_None identified._"},
        })

    # --- Automation / WMS vendors ---
    if automation_vendors:
        vendor_lines = [
            f"• *{v.get('vendor_name', '')}* ({v.get('category', '')}) — {v.get('deployment_status', '')}"
            for v in automation_vendors[:4]
        ]
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Competitors in Account*\n" + "\n".join(vendor_lines),
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Competitors in Account*\n_None identified from public sources._",
            },
        })

    # --- Exception Tax ---
    if exception_tax:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Exception Tax Estimate*\n"
                    f"```{exception_tax.get('math_shown', '')}```"
                ),
            },
        })

    # --- Research gaps ---
    if research_gaps:
        gap_lines = "\n".join(f"• {g}" for g in research_gaps[:5])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Research Gaps (flagged, not guessed)*\n{gap_lines}",
            },
        })

    blocks += [
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"research_actions_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Find contacts →"},
                    "style": "primary",
                    "action_id": "find_contacts",
                    "value": session_id,
                },
            ],
        },
    ]

    return blocks


# ---------------------------------------------------------------------------
# Contact list with deep-research flag buttons — Checkpoint 2
# ---------------------------------------------------------------------------

def contact_list_card(contacts: list, session_id: str, flagged_ids: set = None) -> list:
    """
    Contact list with per-contact flag buttons (⭐ deep research) and a confirm button.
    Rep flags up to 3; bot enforces the cap in the action handler.
    """
    if flagged_ids is None:
        flagged_ids = set()

    flagged_count = len(flagged_ids)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Found *{len(contacts)} contacts*. "
                    f"Flag up to *3* for deep individual research "
                    f"_(flagged: {flagged_count}/3)_, then confirm."
                ),
            },
        },
        {"type": "divider"},
    ]

    for c in contacts:
        pid = c.get("id", "")
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        title = c.get("title", "")
        ptype = c.get("persona_type", "")
        lane = c.get("outreach_lane", "")
        score = c.get("priority_score", "Medium")
        score_emoji = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}.get(score, "🟡")
        is_flagged = pid in flagged_ids

        flag_element = {
            "type": "button",
            "text": {"type": "plain_text", "text": "⭐ Flagged" if is_flagged else "⭐ Flag"},
            "action_id": f"flag_contact_{pid}",
            "value": f"{session_id}:{pid}",
        }
        if is_flagged:
            flag_element["style"] = "primary"

        blocks.append({
            "type": "section",
            "block_id": f"contact_{pid}",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{score_emoji} *{name}* — {title}\n"
                    f"_{ptype}  |  {lane} lane_"
                ),
            },
            "accessory": flag_element,
        })

    blocks += [
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"contact_confirm_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve contacts & build sequences",
                    },
                    "style": "primary",
                    "action_id": "approve_contacts",
                    "value": session_id,
                },
            ],
        },
    ]

    return blocks


# ---------------------------------------------------------------------------
# Session resume card
# ---------------------------------------------------------------------------

def resume_session_card(session) -> list:
    """Prompt rep to continue or cancel an existing active session."""
    phase_labels = {
        2: "company research in progress",
        3: "review the research brief",
        4: "review the contact list",
        5: "sequences being generated",
        6: "review and edit sequences",
    }
    phase_desc = phase_labels.get(session.phase, "in progress")
    account = session.account_name or "your account"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"You have an active session for *{account}* "
                    f"— currently at: _{phase_desc}_.\n\n"
                    "Want to continue, or cancel and start fresh?"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"resume_{session.id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Continue"},
                    "style": "primary",
                    "action_id": "resume_session",
                    "value": session.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel & start fresh"},
                    "style": "danger",
                    "action_id": "cancel_session",
                    "value": session.id,
                },
            ],
        },
    ]


def confirmation_card(
    account_name: str,
    persona_filter: Optional[list],
    use_case_angle: Optional[str],
    session_id: str,
) -> list:
    persona_text = ", ".join(persona_filter) if persona_filter else "All personas"
    angle_text = use_case_angle if use_case_angle else "General outreach"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Got it.* Here's what I'll run:\n\n*Account:* {account_name}\n*Personas:* {persona_text}\n*Angle:* {angle_text}",
            },
        },
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"confirm_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Yes, run it"},
                    "style": "primary",
                    "action_id": "confirm_intent",
                    "value": session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit"},
                    "action_id": "edit_intent",
                    "value": session_id,
                },
            ],
        },
    ]


def clarification_card(question: str, session_id: str) -> list:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Before I run, I need to clarify something:\n\n{question}",
            },
        },
        {
            "type": "input",
            "block_id": f"clarify_{session_id}",
            "element": {
                "type": "plain_text_input",
                "action_id": "clarification_input",
                "placeholder": {"type": "plain_text", "text": "Type your answer here..."},
            },
            "label": {"type": "plain_text", "text": "Your answer"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Submit"},
                    "style": "primary",
                    "action_id": "submit_clarification",
                    "value": session_id,
                }
            ],
        },
    ]


def persona_card(persona: dict, index: int) -> dict:
    score = persona.get("priority_score", "Medium")
    score_emoji = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}.get(score, "🟡")
    lane = persona.get("outreach_lane", "MDR")
    value_driver = persona.get("value_driver", {})
    driver = value_driver.get("primary_driver", "").replace("_", " ").title() if value_driver else ""

    return {
        "type": "section",
        "block_id": f"persona_{persona['id']}",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*{persona.get('first_name')} {persona.get('last_name')}* — {persona.get('title')}\n"
                f"{score_emoji} {score} priority  |  {lane} lane  |  {driver}\n"
                f"_{persona.get('score_reasoning') or ''}_"
            ),
        },
        "accessory": {
            "type": "checkboxes",
            "action_id": f"approve_persona_{persona['id']}",
            "options": [
                {
                    "text": {"type": "plain_text", "text": "Include"},
                    "value": persona["id"],
                }
            ],
        },
    }


def persona_list_card(personas: list, session_id: str) -> list:
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Found *{len(personas)} personas*. Select who to include:",
            },
        },
        {"type": "divider"},
    ]
    for i, persona in enumerate(personas):
        blocks.append(persona_card(persona, i))

    blocks += [
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"persona_confirm_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Confirm & Generate Sequences"},
                    "style": "primary",
                    "action_id": "confirm_personas",
                    "value": session_id,
                }
            ],
        },
    ]
    return blocks


def sequence_step_card(step: dict, persona_name: str, sequence_id: str) -> list:
    channel_emoji = {"email": "📧", "linkedin": "💼", "call": "📞"}.get(step.get("channel"), "📧")
    subject_line = f"\n*Subject:* {step['subject_line']}" if step.get("subject_line") else ""

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{channel_emoji} *Step {step['step_number']}* — Day {step['day_offset']} — {step['channel'].title()}"
                    f"{subject_line}\n\n{step['body']}"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"step_{sequence_id}_{step['step_number']}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_step_{step['step_number']}",
                    "value": f"{sequence_id}:{step['step_number']}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit"},
                    "action_id": f"edit_step_{step['step_number']}",
                    "value": f"{sequence_id}:{step['step_number']}",
                },
            ],
        },
        {"type": "divider"},
    ]


def edit_step_modal(step: dict, sequence_id: str, thread_ts: str) -> dict:
    """Modal view for editing a single sequence step."""
    channel = step.get("channel", "email")
    step_num = step.get("step_number", 1)
    has_subject = channel == "email" and step.get("subject_line")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Step {step_num} — {channel.title()} (Day {step.get('day_offset', 0)})*\n"
                    + (f"Subject: _{step.get('subject_line')}_\n" if has_subject else "")
                    + f"```{(step.get('body') or '')[:500]}```"
                ),
            },
        },
        {
            "type": "input",
            "block_id": "edit_instruction",
            "label": {"type": "plain_text", "text": "What should I change?"},
            "element": {
                "type": "plain_text_input",
                "action_id": "instruction_input",
                "multiline": True,
                "placeholder": {
                    "type": "plain_text",
                    "text": "e.g. 'Make it shorter', 'Focus on labor costs', 'Change the subject line to reference inventory accuracy'",
                },
            },
        },
    ]

    if has_subject:
        blocks.append({
            "type": "input",
            "block_id": "edit_field_select",
            "label": {"type": "plain_text", "text": "What to edit"},
            "element": {
                "type": "static_select",
                "action_id": "field_select",
                "initial_option": {
                    "text": {"type": "plain_text", "text": "Body only"},
                    "value": "body",
                },
                "options": [
                    {"text": {"type": "plain_text", "text": "Body only"}, "value": "body"},
                    {"text": {"type": "plain_text", "text": "Subject line only"}, "value": "subject_line"},
                    {"text": {"type": "plain_text", "text": "Both"}, "value": "both"},
                ],
            },
        })

    return {
        "type": "modal",
        "callback_id": "edit_step_modal_submit",
        "private_metadata": f"{sequence_id}:{step_num}:{thread_ts}",
        "title": {"type": "plain_text", "text": f"Edit Step {step_num}"},
        "submit": {"type": "plain_text", "text": "Apply Edit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def sequence_brief_card(sequence: dict, persona: dict) -> list:
    lane = sequence.get("lane", "MDR")
    steps = sequence.get("steps", [])

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Sequence Brief — {persona.get('first_name')} {persona.get('last_name')}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{persona.get('title')}* at *{persona.get('account_name')}*\n"
                    f"Lane: {lane}  |  {len(steps)} steps  |  Ready to paste into Apollo"
                ),
            },
        },
        {"type": "divider"},
    ]

    for step in steps:
        channel_emoji = {"email": "📧", "linkedin": "💼", "call": "📞"}.get(step.get("channel"), "📧")
        subject = f"*Subject:* `{step['subject_line']}`\n" if step.get("subject_line") else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{channel_emoji} *Step {step['step_number']}* — Day {step['day_offset']}\n"
                    f"{subject}```{step['body']}```"
                ),
            },
        })

    return blocks
