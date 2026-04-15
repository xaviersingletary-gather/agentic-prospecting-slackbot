import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from src.config import settings
from src.integrations.clay import ClayClient
from src.integrations.hubspot import HubSpotClient

logger = logging.getLogger(__name__)

PERSONA_TYPES = ["TDM", "ODM", "FS", "IT", "Safety"]

EXTRACTION_PROMPT = """Extract structured intent from this sales rep message. Return JSON only, no explanation.

Message: "{message}"

Extract:
- account_name: The company name they want to prospect (string or null if unclear)
- persona_filter: List of persona types from ["TDM","ODM","FS","IT","Safety"] based on any role hints mentioned (array or null)
  TDM = technical/CI/automation/engineering roles
  ODM = operations/warehouse/inventory/supply chain roles
  FS = executive/C-suite/SVP/VP roles
  IT = IT/systems/technology roles
  Safety = safety/EHS/risk roles
- use_case_angle: Any specific use case, pain point, or angle mentioned (string or null)

Return only valid JSON:
{{"account_name": "...", "persona_filter": [...] or null, "use_case_angle": "..." or null}}"""


@dataclass
class RepRequest:
    raw_message: str
    rep_id: str
    rep_role: str = "AE"
    channel_id: str = ""
    timestamp: str = ""


@dataclass
class NormalizedRequest:
    account_name: str = ""
    account_domain: str = ""
    company_description: Optional[str] = None
    persona_filter: Optional[list] = None
    use_case_angle: Optional[str] = None
    rep_role: str = "AE"
    confidence: float = 0.0
    clarification_needed: bool = False
    clarification_question: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "account_name": self.account_name,
            "account_domain": self.account_domain,
            "company_description": self.company_description,
            "persona_filter": self.persona_filter,
            "use_case_angle": self.use_case_angle,
            "rep_role": self.rep_role,
            "confidence": self.confidence,
            "clarification_needed": self.clarification_needed,
            "clarification_question": self.clarification_question,
        }


class InputNormalizerAgent:
    def __init__(self):
        self.hubspot = HubSpotClient()
        self.clay = ClayClient()

    def normalize(self, request: RepRequest) -> NormalizedRequest:
        started_at = datetime.utcnow()
        signals = []
        errors = []

        # Step 1: Extract intent via Claude
        extraction = self._extract_intent(request.raw_message)
        account_name = extraction.get("account_name") or ""
        persona_filter = extraction.get("persona_filter")
        use_case_angle = extraction.get("use_case_angle")

        # Validate persona_filter values
        if persona_filter:
            persona_filter = [p for p in persona_filter if p in PERSONA_TYPES] or None

        # Step 2: Enrich account domain + description
        account_domain = ""
        company_description = None

        if account_name:
            hubspot_result = self.hubspot.find_company(account_name)
            if hubspot_result:
                account_domain = hubspot_result.get("domain") or ""
                company_description = hubspot_result.get("description")
            else:
                signals.append("Account not found in HubSpot — tried Clay")
                clay_result = self.clay.find_company(account_name)
                if clay_result:
                    account_domain = clay_result.get("domain") or ""
                    company_description = clay_result.get("description")
                else:
                    signals.append("Account not found in Clay either")

        # Step 3: Calculate confidence
        confidence = self._calculate_confidence(account_name, account_domain, persona_filter, use_case_angle)

        # Step 4: Clarification if needed
        clarification_needed = confidence < 0.7
        clarification_question = None
        if clarification_needed:
            clarification_question = self._generate_clarification(request.raw_message, account_name)
            signals.append(f"Low confidence ({confidence}) — clarification requested")

        result = NormalizedRequest(
            account_name=account_name,
            account_domain=account_domain,
            company_description=company_description,
            persona_filter=persona_filter,
            use_case_angle=use_case_angle,
            rep_role=request.rep_role,
            confidence=confidence,
            clarification_needed=clarification_needed,
            clarification_question=clarification_question,
        )

        duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
        logger.info(
            "normalizer_complete",
            extra={
                "account_name": account_name,
                "confidence": confidence,
                "duration_ms": duration_ms,
                "signals": signals,
            },
        )

        return result

    def _extract_intent(self, raw_message: str) -> dict:
        # Try LLM extraction first
        if settings.OPENROUTER_API_KEY and settings.OPENROUTER_BASE_URL:
            url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
            logger.debug(f"[normalizer] Calling OpenRouter: {url} model={settings.OPENROUTER_MODEL}")
            try:
                response = httpx.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.OPENROUTER_MODEL,
                        "max_tokens": 256,
                        "messages": [{
                            "role": "user",
                            "content": EXTRACTION_PROMPT.format(message=raw_message),
                        }],
                    },
                    timeout=15,
                )
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"].strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                result = json.loads(text)
                logger.info(f"[normalizer] LLM extracted: {result}")
                return result
            except Exception as e:
                logger.warning(f"[normalizer] LLM extraction failed: {e} — falling back to regex")

        # Regex fallback: handles "run outreach for X", "prospect X", "build sequences for X", etc.
        return self._regex_extract(raw_message)

    def _regex_extract(self, raw_message: str) -> dict:
        import re
        text = raw_message.strip()
        account_name = None

        # Pattern 1: explicit command phrases — "run outreach for X", "prospect X", etc.
        command_patterns = [
            r"(?:run outreach for|outreach for|prospect(?:ing)? for|build sequences? for|sequences? for|run prospecting for|prospecting for|target(?:ing)? for)\s+(.+?)(?:\s*$|\s+and\b|\s+focus\b|\s+in\b)",
            r"for\s+([A-Za-z0-9][A-Za-z0-9\s&.,'-]{1,60})(?:\s*$)",
        ]
        for pattern in command_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                account_name = match.group(1).strip().rstrip(".,")
                break

        # Pattern 2: strip common command words and treat remainder as company name
        if not account_name:
            cleaned = re.sub(
                r"^(run|build|do|create|generate|start|make|get|find)?\s*(outreach|prospecting|sequences?|contacts?|personas?)?\s*(for|at|on)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip().rstrip(".,")
            if cleaned and len(cleaned) >= 2:
                account_name = cleaned

        logger.info(f"[normalizer] Regex extracted account_name='{account_name}'")
        return {"account_name": account_name, "persona_filter": None, "use_case_angle": None}

    def _calculate_confidence(
        self,
        account_name: str,
        account_domain: str,
        persona_filter: Optional[list],
        use_case_angle: Optional[str],
    ) -> float:
        score = 0.0
        if account_name:
            score += 0.6
        if account_domain:
            score += 0.2
        if persona_filter or use_case_angle:
            score += 0.2
        return round(min(score, 1.0), 2)

    def _generate_clarification(self, raw_message: str, account_name: str) -> str:
        if not account_name:
            return "Which company did you want to run outreach for?"
        return f"I found *{account_name}* — can you confirm that's the right company, or give me the full name?"
