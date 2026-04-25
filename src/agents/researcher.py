"""
Company Researcher Agent — Phase 2 of the prospecting pipeline.

Orchestrates multi-source research on a target account:
  1. EDGAR 10-K lookup (public companies)
  2. Exa searches across 5 targeted topics
  3. LLM synthesis → structured CompanyResearch output
  4. Deterministic Exception Tax calculation

Calls progress_callback(step_text) after each major step so the caller
can update a live Slack message in place.
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Callable, Optional

import httpx

from src.config import settings
from src.integrations.exa import ExaClient
from src.integrations.edgar import EdgarClient
from src.utils.document_fetcher import fetch_html, extract_10k_sections, html_to_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception Tax — deterministic formula (never LLM-generated)
# ---------------------------------------------------------------------------

def calculate_exception_tax(total_sqft: int, sqft_source: str) -> dict:
    """
    Gather AI Exception Tax formula:
      positions  = totalSqFt × 0.60 × 4 / 36
      savings/yr = positions × 0.025 × 1.0 × 4 × $100 × 0.80
    """
    positions = int(total_sqft * 0.60 * 4 / 36)
    annual_savings = positions * 0.025 * 1.0 * 4 * 100 * 0.80
    annual_savings_mm = round(annual_savings / 1_000_000, 1)

    math_shown = (
        f"Est. sq ft: ~{total_sqft:,}\n"
        f"Pallet positions: {total_sqft:,} × 0.60 × 4 / 36 = ~{positions:,}\n"
        f"Annual savings (2.5% error rate, $100/event, 80% reduction): "
        f"{positions:,} × 0.025 × 4 × $100 × 0.80 = ~${annual_savings:,.0f} "
        f"(~${annual_savings_mm}M/yr)"
    )

    return {
        "total_sqft": total_sqft,
        "pallet_positions": positions,
        "annual_savings_usd": annual_savings,
        "annual_savings_mm": annual_savings_mm,
        "math_shown": math_shown,
        "sqft_source": sqft_source,
    }


# ---------------------------------------------------------------------------
# LLM synthesis prompt
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT = """You are a B2B sales research analyst preparing an account brief for Gather AI, a warehouse drone inventory automation company.

Gather AI sells to warehouse operators with 10+ DCs. Key buying signals: hiring automation/CI/inventory roles, new DCs opening, WMS migrations, shrink/accuracy programs, executive ops mandates.

Extract structured intelligence from the research text below about {company_name}.

Return ONLY valid JSON — no explanation, no markdown code fences:
{{
  "is_public_company": true or false,
  "facility_count": integer or null,
  "facility_count_note": "source or confidence note" or null,
  "total_sqft_estimate": integer (estimated TOTAL sq ft across ALL facilities) or null,
  "sqft_source": "how you estimated this" or null,
  "board_initiatives": [
    {{"title": "string", "summary": "one sentence", "source": "specific document e.g. Q3 2025 earnings call"}}
  ],
  "company_priorities": ["operational priority 1", "operational priority 2"],
  "trigger_events": [
    {{"description": "specific event", "source": "URL or publication", "date": "YYYY-MM or null", "relevance": "why relevant to drone inventory automation"}}
  ],
  "automation_vendors": [
    {{"vendor_name": "string", "category": "WMS|robotics|inventory tech|other", "deployment_status": "deployed|piloting|rumored", "source": "string or null"}}
  ],
  "research_gaps": ["specific data point not found in research"]
}}

RULES:
- board_initiatives: max 3. Include ONLY if sourced from the research text. No guesses.
- company_priorities: max 3. Infer from research context — include supply chain/ops priorities.
- trigger_events: max 5. Include ALL of:
    (a) news events: facility expansions, M&A, leadership changes, audits, shrink incidents
    (b) hiring signals: if the company is actively hiring automation engineers, CI managers, inventory control directors, or similar roles, include each as a trigger event with description="Actively hiring [role title]"
  Must be specific and recent (within 18 months preferred).
- total_sqft_estimate: estimate total sq ft across all facilities. Use facility_count × typical industry sq ft per facility if direct data unavailable.
- research_gaps: explicitly list what was NOT found (e.g. "Facility count not confirmed", "No WMS vendor identified").
- Do NOT hallucinate. If data is absent, add it to research_gaps.

RESEARCH TEXT:
{research_text}"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CompanyResearchAgent:
    def __init__(self):
        self.exa = ExaClient()
        self.edgar = EdgarClient()

    def research(
        self,
        account_name: str,
        account_domain: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        Run full company research. Returns a CompanyResearch dict.
        Calls progress_callback(step_text) after each step.
        """

        def progress(text: str):
            logger.info(f"[researcher] {text}")
            if progress_callback:
                try:
                    progress_callback(text)
                except Exception:
                    pass

        started_at = datetime.utcnow()
        documents_used = []
        doc_text = ""

        # ------------------------------------------------------------------
        # Step 1: EDGAR annual filing lookup (10-K for US, 20-F for foreign)
        # ------------------------------------------------------------------
        progress(f"⏳ Looking up public SEC filings for *{account_name}*...")
        edgar_result = None
        try:
            edgar_result = self.edgar.find_latest_10k(account_name)
            if edgar_result and edgar_result.get("document_url"):
                period = edgar_result.get("period", "recent")
                form_type = edgar_result.get("form_type", "10-K")
                progress(f"✓ Found {period} {form_type} — fetching key sections...")
                content = self.exa.fetch_url_content(
                    edgar_result["document_url"], max_chars=18_000
                )
                if not content:
                    html = fetch_html(edgar_result["document_url"])
                    if html:
                        sections = extract_10k_sections(html)
                        content = "\n\n".join(
                            f"[{k.upper()}]\n{v}"
                            for k, v in sections.items()
                        )

                if content:
                    doc_text = (
                        f"=== {form_type} ANNUAL REPORT ({period}) ===\n"
                        f"Source: {edgar_result['document_url']}\n\n"
                        f"{content[:15_000]}"
                    )
                    documents_used.append({
                        "doc_type": form_type,
                        "source_url": edgar_result["document_url"],
                        "filing_period": period,
                        "entity_name": edgar_result.get("entity_name", account_name),
                    })
                    progress(f"✓ {form_type} sections extracted")
                else:
                    progress(f"⚠️ {form_type} found but content unavailable — using web sources")
            else:
                progress(f"ℹ️ No SEC filing found — likely non-US filer; using web research")
        except Exception as e:
            logger.warning(f"[researcher] EDGAR step failed: {e}")
            progress(f"ℹ️ Public filing lookup unavailable — using web research only")

        # ------------------------------------------------------------------
        # Step 2: Exa topic searches
        # Fetch full content for the three highest-value topics.
        # Run alt query variants for earnings and facilities to widen coverage.
        # Domain-target where we have it (company IR / investor relations site).
        # ------------------------------------------------------------------
        ir_domain = account_domain if account_domain else None

        progress(f"⏳ Searching earnings calls, investor day, and strategic priorities...")
        earnings_hits = self.exa.search_topic(
            account_name, "earnings_board",
            fetch_top_content=True, also_run_alt=True,
            include_domain=ir_domain,
        )

        progress(f"⏳ Searching press releases and expansion announcements...")
        press_hits = self.exa.search_topic(account_name, "press_releases", num_results=6)

        progress(f"⏳ Searching distribution network and facility footprint...")
        facility_hits = self.exa.search_topic(
            account_name, "facilities",
            fetch_top_content=True, also_run_alt=True,
        )

        progress(f"⏳ Searching WMS, automation vendors, and tech stack...")
        automation_hits = self.exa.search_topic(account_name, "automation", num_results=6)

        progress(f"⏳ Searching for trigger events and buying signals...")
        trigger_hits = self.exa.search_topic(
            account_name, "triggers",
            fetch_top_content=True,
        )

        progress(f"⏳ Searching for hiring signals (automation, CI, inventory roles)...")
        hiring_hits = self.exa.search_topic(account_name, "hiring", num_results=6)

        # ------------------------------------------------------------------
        # Step 3: Compile research text + track Exa sources
        # ------------------------------------------------------------------
        research_text = self._compile_research(
            account_name, doc_text,
            earnings_hits, press_hits, facility_hits,
            automation_hits, trigger_hits, hiring_hits,
        )

        # Collect top Exa URLs as source references (one per topic, URL only)
        exa_topic_map = [
            ("Earnings / Board Initiatives", earnings_hits),
            ("Press Releases / News", press_hits),
            ("Facilities / Distribution", facility_hits),
            ("Automation / WMS", automation_hits),
            ("Trigger Events", trigger_hits),
            ("Hiring Signals", hiring_hits),
        ]
        for topic_label, hits in exa_topic_map:
            for h in (hits or [])[:1]:
                url = h.get("url", "")
                if url:
                    documents_used.append({
                        "doc_type": f"Web: {topic_label}",
                        "source_url": url,
                        "filing_period": (h.get("date", "") or "")[:10],
                    })

        # ------------------------------------------------------------------
        # Step 4: LLM synthesis
        # ------------------------------------------------------------------
        progress(f"⏳ Synthesizing research into structured brief...")
        synthesis = self._synthesize(account_name, research_text)

        # ------------------------------------------------------------------
        # Step 5: Exception Tax calculation
        # ------------------------------------------------------------------
        sqft = synthesis.get("total_sqft_estimate")
        exception_tax = None
        if sqft and sqft > 0:
            exception_tax = calculate_exception_tax(
                sqft, synthesis.get("sqft_source", "estimated from research")
            )

        duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
        logger.info(
            f"[researcher] Research complete for '{account_name}' in {duration_ms}ms"
        )

        return {
            "id": str(uuid.uuid4()),
            "account_name": account_name,
            "is_public_company": synthesis.get("is_public_company", False),
            "facility_count": synthesis.get("facility_count"),
            "facility_count_note": synthesis.get("facility_count_note"),
            "total_sqft_estimate": sqft,
            "sqft_source": synthesis.get("sqft_source"),
            "board_initiatives": synthesis.get("board_initiatives", []),
            "company_priorities": synthesis.get("company_priorities", []),
            "trigger_events": synthesis.get("trigger_events", []),
            "automation_vendors": synthesis.get("automation_vendors", []),
            "exception_tax": exception_tax,
            "research_gaps": synthesis.get("research_gaps", []),
            "documents_used": documents_used,
            "raw_research_text": research_text[:15_000],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compile_research(
        self,
        company: str,
        doc_text: str,
        earnings_hits: list,
        press_hits: list,
        facility_hits: list,
        automation_hits: list,
        trigger_hits: list,
        hiring_hits: list = None,
    ) -> str:
        sections = [f"TARGET COMPANY: {company}\n"]

        if doc_text:
            sections.append(doc_text)

        # Priority order: highest-value topics first so truncation hits low-value sections last
        topic_map = [
            ("EARNINGS / BOARD INITIATIVES", earnings_hits),
            ("FACILITIES / DISTRIBUTION NETWORK", facility_hits),
            ("TRIGGER EVENTS", trigger_hits),
            ("HIRING SIGNALS (automation, CI, inventory roles)", hiring_hits or []),
            ("AUTOMATION / WMS VENDORS", automation_hits),
            ("PRESS RELEASES / NEWS", press_hits),
        ]

        for section_label, hits in topic_map:
            if not hits:
                continue
            parts = [f"\n=== {section_label} ==="]
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
                # Include full article content when available (top result only)
                if h.get("full_content"):
                    parts.append(f"Full article:\n{h['full_content'][:4_000]}")
                parts.append("")
            sections.append("\n".join(parts))

        return "\n".join(sections)

    def _synthesize(self, company_name: str, research_text: str) -> dict:
        """Call the LLM to synthesize raw research into structured JSON."""
        if not settings.OPENROUTER_API_KEY:
            logger.warning("[researcher] OPENROUTER_API_KEY not set — returning empty synthesis")
            return {"research_gaps": ["LLM synthesis unavailable — OPENROUTER_API_KEY not set"]}

        prompt = _SYNTHESIS_PROMPT.format(
            company_name=company_name,
            research_text=research_text[:20_000],
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
                    "max_tokens": 2000,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45,
            )
            response.raise_for_status()
            raw_text = response.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.rsplit("```", 1)[0]

            result = json.loads(raw_text.strip())
            logger.info(f"[researcher] LLM synthesis succeeded for '{company_name}'")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[researcher] JSON parse error in synthesis: {e}")
            return {"research_gaps": ["LLM synthesis returned invalid JSON"]}
        except Exception as e:
            logger.error(f"[researcher] LLM synthesis failed: {e}")
            return {"research_gaps": [f"LLM synthesis error: {str(e)[:100]}"]}
