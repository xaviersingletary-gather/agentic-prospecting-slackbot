import json
import logging
import re
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

THEMES: dict[str, dict] = {
    "volatile_market": {
        "id": "volatile_market",
        "display_name": "Volatile Market: Control What You Can",
        "lead_stat": "$1.77 trillion lost annually to inventory distortion — not theft, not damage. Bad data.",
        "signals": [
            "tariff", "tariffs", "trade policy", "freight rate", "supply chain disruption",
            "carrying cost", "inventory carrying", "labor pressure", "geopolitical",
            "oil price", "inventory distortion", "macro", "uncertainty",
        ],
        "persona_openers": {
            "exec": "The market is unpredictable. Your warehouse operations don't have to be.",
            "ci_tech": "Finding savings is hard when your data's already wrong.",
            "ops": "External chaos is loud. Your warehouse data should be clear.",
        },
        "follow_up_content": {
            "all": "What Inventory Distortion Really Costs",
            "ODM": "Yes, Warehouses Lose Inventory In Their Own Buildings",
        },
    },
    "ai_data_foundation": {
        "id": "ai_data_foundation",
        "display_name": "AI Initiatives Need a Data Foundation",
        "lead_stat": "70% improved inventory accuracy across Gather AI customer deployments. Most AI tools are built on data that's already wrong.",
        "signals": [
            "artificial intelligence", "machine learning", "ai initiative", "ai investment",
            "demand forecast", "wms migration", "wms upgrade", "digital transformation",
            "ai planning", "automation initiative", "generative ai", "data foundation",
        ],
        "persona_openers": {
            "exec": "Your AI investments are only as good as the data beneath them.",
            "ci_tech": "If the data feeding your processes is wrong, the improvements don't stick.",
            "ops": "Your WMS is your system of record, not your system of reality.",
        },
        "follow_up_content": {
            "all": "How Gather AI Delivers Superior Inventory Intelligence",
            "TDM": "The Rise of Agentic Intralogistics",
            "IT": "The Rise of Agentic Intralogistics",
        },
    },
    "labor_cycle_count": {
        "id": "labor_cycle_count",
        "display_name": "Labor Costs + Cycle Count Waste",
        "lead_stat": "NFI cut cycle counting from 4,400 hrs/year to 800. Barrett reallocated 6 cycle counters and saved $250k in MHE at one facility.",
        "signals": [
            "cycle count", "cycle counting", "inventory count", "labor cost", "turnover",
            "headcount", "new dc", "new distribution center", "facility expansion",
            "warehouse labor", "manual count", "counter", "labor shortage",
        ],
        "persona_openers": {
            "exec": "At one facility, 4,400 hours down to 800. Multiply that across your network.",
            "ci_tech": "6 people climbing racks. Now it's 1 person and a drone.",
            "ops": "Stop sending your best people to do your worst work.",
        },
        "follow_up_content": {
            "FS": "GEODIS: 5x productivity, overtime eliminated",
            "Executive Sponsor": "GEODIS: 5x productivity, overtime eliminated",
            "TDM": "Barrett: $250k MHE savings, 6 counters to 1",
            "IT": "Barrett: $250k MHE savings, 6 counters to 1",
            "ODM": "NFI: 75% reduction in inventory counting time",
        },
    },
    "physical_ai_proof": {
        "id": "physical_ai_proof",
        "display_name": "Physical AI Recognition: Third-Party Proof",
        "is_fallback": True,
        "lead_stat": "#10 Fast Company Most Innovative Companies in Logistics 2026. #22 Inc. Regionals Northeast fastest-growing 2026.",
        "signals": [],
        "persona_openers": {
            "exec": "Fast Company just ranked us #10 most innovative in logistics.",
            "ci_tech": "Not a pilot. Deployed, scaling, expanding site by site.",
            "ops": "Inc. Magazine ranked us #22 fastest-growing in the Northeast.",
        },
        "follow_up_content": {
            "FS": "Fast Company Most Innovative Companies 2026",
            "Executive Sponsor": "Fast Company Most Innovative Companies 2026",
            "ODM": "Inc. Regionals: Northeast 2026",
            "TDM": "All customer case studies",
            "IT": "All customer case studies",
            "all": "All customer case studies",
        },
    },
}

PERSONA_TO_BUCKET: dict[str, str] = {
    "FS": "exec",
    "Executive Sponsor": "exec",
    "TDM": "ci_tech",
    "IT": "ci_tech",
    "ODM": "ops",
    "Safety": "ops",
}

_LLM_PROMPT = """You are a B2B sales messaging expert. Account research is shown below. Select the best content theme for this account.

Themes:
1. volatile_market — tariff exposure, supply chain disruption, carrying costs, freight volatility, labor pressure
2. ai_data_foundation — AI/ML hiring or investment, WMS migration/upgrade, digital transformation, demand forecasting
3. labor_cycle_count — cycle counter headcount, manual counting, labor cost pressure, new DC openings
4. physical_ai_proof — fallback when signals are thin; cold outreach with no strong match

RESEARCH:
{research_text}

Return ONLY valid JSON, no markdown:
{{"primary_theme_id": "theme_id", "secondary_theme_id": "theme_id or null", "rationale": "1 sentence", "matched_signals": ["signal1"]}}"""


def _flatten_research(research_data: dict) -> str:
    parts = []
    for te in (research_data.get("trigger_events") or [])[:5]:
        if te.get("description"):
            parts.append(f"Trigger: {te['description']}")
    for bi in (research_data.get("board_initiatives") or [])[:3]:
        if bi.get("title"):
            parts.append(f"Initiative: {bi['title']} — {bi.get('summary', '')}")
    for cp in (research_data.get("company_priorities") or [])[:3]:
        parts.append(f"Priority: {cp}")
    raw = (research_data.get("raw_research_text") or "")[:1500]
    if raw:
        parts.append(raw)
    return "\n".join(parts) if parts else ""


class ThemeRouterAgent:
    def route(self, research_data: dict, approved_personas: list[dict]) -> dict:
        primary_id, secondary_id, signals, rationale, method = self._select_themes(research_data)
        return {
            "primary_theme_id": primary_id,
            "secondary_theme_id": secondary_id,
            "selection_rationale": rationale,
            "matched_signals": signals,
            "persona_assignments": self._assign_variants(primary_id, approved_personas),
            "method": method,
        }

    def _select_themes(self, research_data: dict) -> tuple:
        scores = self._keyword_score(research_data)
        ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

        top_id, (top_score, top_signals) = ranked[0]
        second_id, (second_score, _) = ranked[1]

        if top_score >= 3 and (top_score - second_score) >= 2:
            secondary = second_id if second_score >= 2 else None
            return top_id, secondary, top_signals, f"Keyword match: {', '.join(top_signals[:3])}", "keyword"

        if settings.OPENROUTER_API_KEY:
            result = self._llm_select(research_data)
            if result:
                return (
                    result["primary_theme_id"],
                    result.get("secondary_theme_id"),
                    result.get("matched_signals", []),
                    result.get("rationale", "LLM selection"),
                    "llm",
                )

        if top_score > 0:
            return top_id, None, top_signals, f"Keyword match: {', '.join(top_signals[:2])}", "keyword"

        return "physical_ai_proof", None, [], "No strong signals — defaulting to credibility theme", "fallback"

    def _keyword_score(self, research_data: dict) -> dict[str, tuple]:
        text = _flatten_research(research_data).lower()
        result = {}
        for theme_id, theme in THEMES.items():
            if theme.get("is_fallback"):
                result[theme_id] = (0, [])
                continue
            matched = [s for s in theme["signals"] if s.lower() in text]
            result[theme_id] = (len(matched), matched)
        return result

    def _llm_select(self, research_data: dict) -> Optional[dict]:
        try:
            prompt = _LLM_PROMPT.format(research_text=_flatten_research(research_data))
            response = httpx.post(
                f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "max_tokens": 200,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw.strip())
            data = json.loads(raw)
            if data.get("primary_theme_id") in THEMES:
                return data
        except Exception as e:
            logger.warning(f"[theme_router] LLM selection failed: {e}")
        return None

    def _assign_variants(self, theme_id: str, personas: list[dict]) -> list[dict]:
        theme = THEMES.get(theme_id) or THEMES["physical_ai_proof"]
        assignments = []
        for p in personas:
            persona_type = p.get("persona_type", "")
            bucket = PERSONA_TO_BUCKET.get(persona_type, "ops")
            opener = theme["persona_openers"].get(bucket, "")
            follow_up = (
                theme["follow_up_content"].get(persona_type)
                or theme["follow_up_content"].get(bucket)
                or theme["follow_up_content"].get("all", "")
            )
            assignments.append({
                "persona_id": p["id"],
                "persona_type": persona_type,
                "messaging_bucket": bucket,
                "theme_id": theme_id,
                "opener": opener,
                "lead_stat": theme["lead_stat"],
                "follow_up_content": follow_up,
            })
        return assignments
