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
    documents_used = research.get("documents_used") or []

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
        for t in trigger_events[:5]:
            date_str = f" ({t.get('date', '')})" if t.get("date") else ""
            src = t.get("source", "")
            if src and src.startswith("http"):
                src_str = f" — <{src}|source>"
            elif src:
                src_str = f" — _{src}_"
            else:
                src_str = ""
            relevance = f"\n  _↳ {t.get('relevance', '')}_" if t.get("relevance") else ""
            trigger_lines.append(
                f"• {t.get('description', '')}{date_str}{src_str}{relevance}"
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

    # --- Sources ---
    sec_docs = [d for d in documents_used if d.get("doc_type") in ("10-K", "20-F")]
    web_docs = [d for d in documents_used if d.get("doc_type", "").startswith("Web:")]

    source_lines = []
    for doc in sec_docs:
        form = doc.get("doc_type", "Filing")
        period = doc.get("filing_period", "")
        entity = doc.get("entity_name", "")
        url = doc.get("source_url", "")
        label = f"{form} ({period})" if period else form
        if entity:
            label += f" — {entity}"
        source_lines.append(f"• <{url}|{label}>" if url else f"• {label}")

    for doc in web_docs[:3]:
        topic = doc.get("doc_type", "Web").replace("Web: ", "")
        url = doc.get("source_url", "")
        date = doc.get("filing_period", "")
        date_str = f" ({date})" if date else ""
        source_lines.append(f"• <{url}|{topic}>{date_str}" if url else f"• {topic}")

    if source_lines:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Sources*\n" + "\n".join(source_lines),
            },
        })
    else:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Sources: Exa web search (no SEC filing found)_"}],
        })

    return blocks


# ---------------------------------------------------------------------------
# Contact list with deep-research flag buttons — Checkpoint 2
# ---------------------------------------------------------------------------

def sales_play_card(play_data: dict, account_name: str) -> list:
    """
    Render the AE game plan card from SalesPlayAgent output.
    Appears between the research brief and the contact list.
    """
    if play_data.get("error"):
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*AE Game Plan — {account_name}*\n"
                        f"_Analysis unavailable: {play_data['error']}_"
                    ),
                },
            },
        ]

    blocks: list = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"AE Game Plan — {account_name}"},
        },
    ]

    def _trunc(text: str, limit: int) -> str:
        text = (text or "").strip()
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"

    icp_fit = _trunc(play_data.get("icp_fit_summary") or "", 160)
    if icp_fit:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{icp_fit}_"},
        })

    blocks.append({"type": "divider"})

    # Entry point
    entry = play_data.get("entry_point") or {}
    if entry:
        contact_str = ""
        if entry.get("contact_name"):
            contact_str = f"\n*Contact:* {entry['contact_name']}"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*🎯 Start Here — {entry.get('persona_type', '')}*"
                    f"{contact_str}\n"
                    f"*Why:* {_trunc(entry.get('rationale', ''), 100)}\n"
                    f"*First move:* {_trunc(entry.get('first_move', ''), 120)}"
                ),
            },
        })
        blocks.append({"type": "divider"})

    # Plays
    plays = play_data.get("plays") or []
    for i, play in enumerate(plays[:2], 1):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Play {i}: {play.get('name', '')}*\n"
                    f"• *Trigger:* {_trunc(play.get('trigger', ''), 80)}\n"
                    f"• *Target:* {play.get('target_persona', '')}\n"
                    f"• *Approach:* {_trunc(play.get('approach', ''), 120)}\n"
                    f"• *Talk track:* _{_trunc(play.get('talk_track', ''), 100)}_\n"
                    f"• *Hook:* {_trunc(play.get('meeting_hook', ''), 100)}"
                ),
            },
        })

    blocks.append({"type": "divider"})

    fs_path = _trunc(play_data.get("financial_sponsor_path") or "", 200)
    if fs_path:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*💰 FS Path*\n{fs_path}",
            },
        })

    urgency = play_data.get("urgency_drivers") or []
    if urgency:
        urgency_lines = "\n".join(f"• {_trunc(u, 80)}" for u in urgency[:2])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⚡ Urgency*\n{urgency_lines}",
            },
        })

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

        email = c.get("email") or ""
        linkedin_url = c.get("linkedin_url") or ""
        detail_parts = []
        if email:
            detail_parts.append(f"📧 {email}")
        if linkedin_url:
            detail_parts.append(f"<{linkedin_url}|💼 LinkedIn>")
        detail_line = ("  |  ".join(detail_parts)) if detail_parts else ""

        blocks.append({
            "type": "section",
            "block_id": f"contact_{pid}",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{score_emoji} *{name}* — {title}\n"
                    f"_{ptype}  |  {lane} lane_"
                    + (f"\n{detail_line}" if detail_line else "")
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


def edit_confirmation_card(
    account_name: str,
    persona_filter: Optional[list],
    use_case_angle: Optional[str],
    session_id: str,
) -> list:
    persona_text = ", ".join(persona_filter) if persona_filter else ""
    angle_text = use_case_angle or ""

    account_element: dict = {"type": "plain_text_input", "action_id": "edit_account_input"}
    if account_name:
        account_element["initial_value"] = account_name

    personas_element: dict = {
        "type": "plain_text_input",
        "action_id": "edit_personas_input",
        "placeholder": {"type": "plain_text", "text": "e.g. TDM, ODM — leave blank for all"},
    }
    if persona_text:
        personas_element["initial_value"] = persona_text

    angle_element: dict = {
        "type": "plain_text_input",
        "action_id": "edit_angle_input",
        "placeholder": {"type": "plain_text", "text": "e.g. inventory accuracy, labor reduction"},
    }
    if angle_text:
        angle_element["initial_value"] = angle_text

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Edit your request:*"}},
        {
            "type": "input",
            "block_id": f"edit_account_{session_id}",
            "element": account_element,
            "label": {"type": "plain_text", "text": "Account"},
        },
        {
            "type": "input",
            "block_id": f"edit_personas_{session_id}",
            "optional": True,
            "element": personas_element,
            "label": {"type": "plain_text", "text": "Personas (comma-separated, or blank for all)"},
        },
        {
            "type": "input",
            "block_id": f"edit_angle_{session_id}",
            "optional": True,
            "element": angle_element,
            "label": {"type": "plain_text", "text": "Outreach angle (or blank for general)"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Save"},
                    "style": "primary",
                    "action_id": "submit_edit",
                    "value": session_id,
                }
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


def all_sequences_approval_card(
    session_id: str,
    persona_names: list,
    theme_info: Optional[dict] = None,
) -> list:
    names_text = "\n".join(f"• {n}" for n in persona_names) if persona_names else "• (no sequences)"
    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Sequences ready for review:*\n{names_text}\n\n"
                    "Edit any step above, then approve all when ready."
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"approve_all_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve all & deliver briefs"},
                    "style": "primary",
                    "action_id": "approve_all_sequences",
                    "value": session_id,
                },
            ],
        },
    ]
    if theme_info and theme_info.get("name"):
        rationale = theme_info.get("rationale", "")
        context_text = f"Theme: *{theme_info['name']}*"
        if rationale:
            context_text += f" — _{rationale}_"
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": context_text}],
        })
    return blocks


def session_complete_card(account_name: str, sequence_count: int, persona_names: list) -> list:
    names_text = ", ".join(persona_names) if persona_names else "N/A"
    return [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Done — {account_name}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{sequence_count} sequence(s) delivered*\n"
                    f"*Personas:* {names_text}\n\n"
                    "Briefs are ready to paste. Start a new account anytime."
                ),
            },
        },
    ]


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
