from typing import Optional


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
