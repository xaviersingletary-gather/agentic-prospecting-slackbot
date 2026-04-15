import json
import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

EDIT_SYSTEM_PROMPT = """You are an expert B2B sales copywriter specializing in outbound sequences for \
warehouse automation technology (Gather AI — autonomous drone inventory counting).

Your job is to apply a rep's edit instruction to a single sequence step. Return ONLY the edited copy — \
no explanations, no preamble, no JSON wrapper. Just the final text for the field being edited.

Rules:
- Keep the same tone: direct, credible, concise, not pushy
- Preserve the core value proposition unless the rep explicitly changes it
- Never exceed the original length by more than 30% unless the rep asks to expand
- For subject lines: keep under 60 characters
- For LinkedIn messages: keep under 300 characters
- For emails: 3-5 short paragraphs max"""

EDIT_USER_TEMPLATE = """Current step:
Channel: {channel}
Subject line: {subject_line}
Body:
{body}

Edit instruction: {instruction}

{field_instruction}"""


class SequenceEditorAgent:
    def apply_edit(
        self,
        step: dict,
        instruction: str,
        edit_field: str = "body",  # "body" | "subject_line" | "both"
    ) -> dict:
        """
        Apply a rep's natural language instruction to a sequence step.
        Returns the updated step dict. Falls back to original on failure.
        """
        if edit_field == "subject_line":
            field_instruction = "Return ONLY the new subject line text."
        elif edit_field == "both":
            field_instruction = (
                "Return the updated content as JSON: "
                "{\"subject_line\": \"...\", \"body\": \"...\"}"
            )
        else:
            field_instruction = "Return ONLY the new body text."

        prompt = EDIT_USER_TEMPLATE.format(
            channel=step.get("channel", "email"),
            subject_line=step.get("subject_line") or "(none)",
            body=step.get("body", ""),
            instruction=instruction,
            field_instruction=field_instruction,
        )

        result = self._call_llm(prompt)
        if result is None:
            logger.warning("[editor] LLM call failed — returning original step unchanged")
            return step

        updated = dict(step)

        if edit_field == "both":
            try:
                parsed = json.loads(result)
                updated["subject_line"] = parsed.get("subject_line", step.get("subject_line"))
                updated["body"] = parsed.get("body", step.get("body"))
            except json.JSONDecodeError:
                # LLM didn't return JSON — apply as body only
                updated["body"] = result
        elif edit_field == "subject_line":
            updated["subject_line"] = result.strip()
        else:
            updated["body"] = result

        logger.info(
            f"[editor] Applied edit to step {step.get('step_number')} "
            f"({step.get('channel')}) — field={edit_field}"
        )
        return updated

    def _call_llm(self, user_message: str) -> Optional[str]:
        if not settings.OPENROUTER_API_KEY or not settings.OPENROUTER_BASE_URL:
            return None
        try:
            response = httpx.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "max_tokens": 512,
                    "messages": [
                        {"role": "system", "content": EDIT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                },
                timeout=20,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[editor] LLM call failed: {e}")
            return None
