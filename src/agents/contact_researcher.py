"""
Individual Contact Researcher Agent — runs only on rep-flagged contacts (max 3).

For each contact, searches for:
  - Current role tenure
  - Prior 1-2 roles
  - Recent LinkedIn posts / public statements
  - Speaking / conference activity

Uses LLM synthesis to structure raw Exa snippets into ContactResearch output.
"""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import httpx

from src.config import settings
from src.integrations.exa import ExaClient

logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = """You are a B2B sales researcher. Extract information about a specific person from the research snippets below.

Person: {full_name}
Title: {title}
Company: {company}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "current_role_tenure": "approximate tenure e.g. '2 years' or '6 months'" or null,
  "prior_roles": [
    {{"title": "string", "company": "string", "duration": "string or null"}}
  ],
  "recent_linkedin": [
    {{"type": "recent_post", "content": "brief summary of what they posted/said", "date": "YYYY-MM-DD or null"}}
  ],
  "speaking_activity": "brief description of any conference talk, quote, or published statement found" or null,
  "research_gaps": ["what was not found"]
}}

RULES:
- prior_roles: max 2 most recent roles before current position only.
- recent_linkedin: max 2 most relevant. Only include if clearly attributable to this person.
- speaking_activity: only include if a specific talk, quote, or byline was found.
- research_gaps: list what wasn't found (e.g. "No LinkedIn activity found", "No prior role history found").
- Do NOT hallucinate. If data isn't in the research, add to research_gaps.

RESEARCH TEXT:
{research_text}"""


class ContactResearchAgent:
    def __init__(self):
        self.exa = ExaClient()

    def research_contacts(
        self,
        contacts: list[dict],
        progress_callback=None,
    ) -> dict[str, dict]:
        """
        Research multiple contacts in parallel (max 3 enforced by caller).
        Returns {persona_id: contact_research_dict}.
        """
        if not contacts:
            return {}

        def progress(text: str):
            if progress_callback:
                try:
                    progress_callback(text)
                except Exception:
                    pass

        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self._research_one, c): c
                for c in contacts
            }
            for future in as_completed(futures):
                contact = futures[future]
                name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
                try:
                    research = future.result()
                    results[contact["id"]] = research
                    progress(f"✓ Individual research complete: {name}")
                except Exception as e:
                    logger.warning(f"[contact_researcher] Research failed for {name}: {e}")
                    results[contact["id"]] = self._empty_research(contact["id"])
                    progress(f"⚠️ Could not research {name} — continuing without individual context")

        return results

    def _research_one(self, contact: dict) -> dict:
        """Research a single contact and return structured ContactResearch dict."""
        first = contact.get("first_name", "")
        last = contact.get("last_name", "")
        title = contact.get("title", "")
        company = contact.get("account_name", "")
        full_name = f"{first} {last}".strip()

        logger.info(f"[contact_researcher] Researching {full_name} at {company}")

        # Exa search for this person
        hits = self.exa.search_topic(
            company_name=company,
            topic="contact",
            extra_tokens={"first_name": first, "last_name": last, "title": title},
            num_results=5,
        )

        # Compile research text
        parts = [f"Person: {full_name}", f"Title: {title}", f"Company: {company}", ""]
        for h in hits:
            if not (h.get("headline") or h.get("snippet")):
                continue
            date = h.get("date", "")[:10] if h.get("date") else ""
            url = h.get("url", "")
            parts.append(f"[{date} | {url}]")
            if h.get("headline"):
                parts.append(f"Headline: {h['headline']}")
            if h.get("snippet"):
                parts.append(f"Excerpt: {h['snippet']}")
            parts.append("")
        research_text = "\n".join(parts)

        # LLM synthesis
        synthesis = self._synthesize(full_name, title, company, research_text)

        return {
            "id": str(uuid.uuid4()),
            "persona_id": contact["id"],
            "session_id": contact.get("session_id", ""),
            "current_role_tenure": synthesis.get("current_role_tenure"),
            "prior_roles": synthesis.get("prior_roles", []),
            "recent_linkedin": synthesis.get("recent_linkedin", []),
            "speaking_activity": synthesis.get("speaking_activity"),
            "research_gaps": synthesis.get("research_gaps", []),
            "created_at": datetime.utcnow().isoformat(),
        }

    def _synthesize(self, full_name: str, title: str, company: str, research_text: str) -> dict:
        if not settings.OPENROUTER_API_KEY:
            return {"research_gaps": ["LLM unavailable"]}

        prompt = _SYNTHESIS_PROMPT.format(
            full_name=full_name,
            title=title,
            company=company,
            research_text=research_text[:6_000],
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
                    "max_tokens": 800,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0]
            return json.loads(raw.strip())
        except json.JSONDecodeError as e:
            logger.error(f"[contact_researcher] JSON parse error for {full_name}: {e}")
            return {"research_gaps": ["LLM returned invalid JSON"]}
        except Exception as e:
            logger.error(f"[contact_researcher] LLM call failed for {full_name}: {e}")
            return {"research_gaps": [f"Research error: {str(e)[:80]}"]}

    @staticmethod
    def _empty_research(persona_id: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "persona_id": persona_id,
            "session_id": "",
            "current_role_tenure": None,
            "prior_roles": [],
            "recent_linkedin": [],
            "speaking_activity": None,
            "research_gaps": ["Research unavailable"],
            "created_at": datetime.utcnow().isoformat(),
        }
