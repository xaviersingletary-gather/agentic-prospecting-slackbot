"""
Sales Play Agent — synthesizes company research + discovered contacts into
AE-level meeting-booking plays. Runs after persona discovery, before the
contact list is shown to the rep.
"""
import json
import logging
import re as _re

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

_PLAYS_PROMPT = """You are a senior AE at Gather AI — a warehouse drone inventory automation company.

Gather AI automates inventory counting with autonomous drones. You sell to warehouse operators with 10+ distribution centers. Core value props:
- Eliminate manual cycle counting labor (15x faster)
- 99%+ inventory location accuracy
- Reduce shrink write-offs with a measurable ROI
- Replicable deployment blueprint for network-wide standardization

BUYER PERSONAS:
- TDM (Technical Decision Maker): Director CI, Automation Manager, Industrial Engineer — primary entry point, veto power, thinks in systems and processes
- ODM (Operational Decision Maker): VP/Director Operations, Fulfillment, Inventory Control — validates floor fit; without buy-in, deployment fails
- FS (Financial Sponsor): VP/Director Finance — #1 closed-lost gap at Gather AI; must engage early; owns budget approval
- IT Stakeholder: VP IT, Director IT — integration and security veto risk
- Executive Sponsor: COO, Chief Supply Chain Officer — needed for pilot-to-network expansion

ICP FIT SIGNALS (strong buying intent):
- 10+ distribution/fulfillment centers in North America
- WMS of record (Blue Yonder, Manhattan, SAP, or equivalent)
- Revenue $500M+
- Actively hiring automation, CI, or inventory control roles
- Opening new DCs or WMS migration underway
- Competitor has deployed autonomous inventory tech

ACCOUNT RESEARCH:
{research_summary}

CONTACTS FOUND AT THIS ACCOUNT:
{contacts_summary}

Return ONLY valid JSON — no markdown fences, no explanation. Be extremely concise — every value is a Slack message, not a paragraph.
{{
  "icp_fit_summary": "1 sentence max",
  "entry_point": {{
    "persona_type": "TDM|ODM|FS|IT|Executive Sponsor",
    "contact_name": "matched contact name or null",
    "rationale": "10-15 words — why start here",
    "first_move": "channel + hook + ask in 1 sentence"
  }},
  "plays": [
    {{
      "name": "3-5 words",
      "trigger": "the specific signal, 10 words max",
      "target_persona": "persona type",
      "approach": "1 sentence — the angle and why it lands here",
      "talk_track": "the opening line or subject, account-specific",
      "meeting_hook": "1 sentence — why take the meeting now"
    }}
  ],
  "financial_sponsor_path": "1-2 sentences — who, when, how to frame",
  "urgency_drivers": ["signal 1", "signal 2 if applicable"]
}}

RULES:
- 2 plays max. Each grounded in a specific research signal.
- entry_point.contact_name: only if a discovered contact clearly matches the persona type
- financial_sponsor_path: always populate — #1 deal failure point at Gather AI
- If research signals are thin, say so in icp_fit_summary and keep plays tight
- Think like an AE making calls tomorrow morning"""


def _build_research_summary(research_data: dict) -> str:
    parts = []

    facility_count = research_data.get("facility_count")
    if facility_count:
        note = research_data.get("facility_count_note", "")
        note_str = f" ({note})" if note else ""
        parts.append(f"Facility count: ~{facility_count} DCs/facilities{note_str}")

    board_initiatives = research_data.get("board_initiatives") or []
    if board_initiatives:
        inits = "; ".join(
            f"{i.get('title', '')}: {i.get('summary', '')}"
            for i in board_initiatives[:3]
        )
        parts.append(f"Board/exec initiatives: {inits}")

    company_priorities = research_data.get("company_priorities") or []
    if company_priorities:
        parts.append(f"Operational priorities: {'; '.join(company_priorities[:3])}")

    trigger_events = research_data.get("trigger_events") or []
    if trigger_events:
        triggers = "; ".join(
            f"{t.get('description', '')} ({t.get('date', 'no date')})"
            for t in trigger_events[:5]
        )
        parts.append(f"Trigger events: {triggers}")

    automation_vendors = research_data.get("automation_vendors") or []
    if automation_vendors:
        vendors = "; ".join(
            f"{v.get('vendor_name', '')} ({v.get('category', '')}, {v.get('deployment_status', '')})"
            for v in automation_vendors[:4]
        )
        parts.append(f"Automation/WMS vendors in account: {vendors}")

    exception_tax = research_data.get("exception_tax") or {}
    if exception_tax.get("annual_savings_mm"):
        parts.append(
            f"Estimated Exception Tax savings opportunity: ~${exception_tax['annual_savings_mm']}M/yr"
        )

    research_gaps = research_data.get("research_gaps") or []
    if research_gaps:
        parts.append(f"Research gaps: {'; '.join(research_gaps[:3])}")

    return "\n".join(parts) if parts else "Minimal structured research data available."


def _build_contacts_summary(contacts: list[dict]) -> str:
    if not contacts:
        return "No contacts found."
    lines = []
    for c in contacts[:8]:
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        title = c.get("title", "")
        ptype = c.get("persona_type", "")
        seniority = c.get("seniority", "")
        lane = c.get("outreach_lane", "")
        lines.append(f"- {name} | {title} | {ptype} | {seniority} | {lane} lane")
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """Try multiple strategies to extract a JSON object from an LLM response."""
    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences then parse
    stripped = _re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=_re.IGNORECASE)
    stripped = _re.sub(r"\s*```$", "", stripped.strip())
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 3. Extract the first {...} block (handles leading/trailing prose)
    match = _re.search(r"\{.*\}", text, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


class SalesPlayAgent:
    def generate(
        self,
        research_data: dict,
        contacts: list[dict],
        account_name: str,
    ) -> dict:
        """
        Synthesize research + contacts into AE-level plays.
        Returns a structured dict. Never raises — returns {"error": "..."} on failure.
        """
        if not settings.OPENROUTER_API_KEY:
            logger.warning("[sales_play] OPENROUTER_API_KEY not set — skipping")
            return {"error": "LLM synthesis unavailable — OPENROUTER_API_KEY not set"}

        research_summary = _build_research_summary(research_data)
        contacts_summary = _build_contacts_summary(contacts)

        prompt = _PLAYS_PROMPT.format(
            research_summary=research_summary,
            contacts_summary=contacts_summary,
        )

        try:
            response = httpx.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "max_tokens": 1500,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=40,
            )
            response.raise_for_status()
            raw_text = response.json()["choices"][0]["message"]["content"].strip()

            result = _extract_json(raw_text)
            if result is None:
                logger.error(f"[sales_play] JSON parse failed. Raw response:\n{raw_text[:500]}")
                return {"error": "Sales play generation returned invalid JSON"}

            logger.info(f"[sales_play] Plays generated for '{account_name}'")
            return result
        except Exception as e:
            logger.error(f"[sales_play] Generation failed: {e}")
            return {"error": f"Sales play generation error: {str(e)[:100]}"}
