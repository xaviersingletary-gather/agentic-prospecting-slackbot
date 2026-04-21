"""
SEC EDGAR integration — finds a company's latest 10-K filing and returns the document URL.
Uses the EDGAR EFTS full-text search API (no API key required).
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{primary_doc}"
_HEADERS = {
    "User-Agent": "GatherAI-Prospecting-Bot/1.0 research@gather.ai",
    "Accept": "application/json",
}


class EdgarClient:
    """Lightweight EDGAR client for locating 10-K filings."""

    def find_latest_10k(self, company_name: str) -> Optional[dict]:
        """
        Search EDGAR for a company's latest 10-K filing.
        Returns dict with keys: entity_name, file_date, period, cik, accession_no, document_url
        Returns None if not found or on error.
        """
        hit = self._efts_search(company_name)
        if not hit:
            return None

        accession_no = hit.get("accession_no", "")
        if not accession_no:
            return None

        # CIK is the numeric prefix of the accession number (e.g. "0000354950-24-000012" → 354950)
        try:
            cik = int(accession_no.split("-")[0])
        except (ValueError, IndexError):
            logger.warning(f"[edgar] Could not parse CIK from accession_no: {accession_no}")
            return None

        document_url = self._get_primary_document_url(cik, accession_no)

        return {
            "entity_name": hit.get("entity_name", company_name),
            "file_date": hit.get("file_date", ""),
            "period": hit.get("period_of_report", ""),
            "cik": cik,
            "accession_no": accession_no,
            "document_url": document_url,
        }

    def _efts_search(self, company_name: str) -> Optional[dict]:
        """Search EFTS for the most recent 10-K matching the company name."""
        try:
            resp = httpx.get(
                _EFTS_URL,
                params={
                    "q": f'"{company_name}"',
                    "forms": "10-K",
                    "dateRange": "custom",
                    "startdt": "2022-01-01",
                    "enddt": "2026-12-31",
                },
                headers=_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            if not hits:
                logger.info(f"[edgar] No 10-K found for '{company_name}'")
                return None
            # Sort by file_date descending — take the most recent
            hits.sort(key=lambda h: h.get("_source", {}).get("file_date", ""), reverse=True)
            return hits[0].get("_source", {})
        except Exception as e:
            logger.warning(f"[edgar] EFTS search failed for '{company_name}': {e}")
            return None

    def _get_primary_document_url(self, cik: int, accession_no: str) -> Optional[str]:
        """
        Look up the primary 10-K document filename via the SEC submissions API,
        then construct the full document URL.
        """
        try:
            cik_padded = str(cik).zfill(10)
            resp = httpx.get(
                _SUBMISSIONS_URL.format(cik=cik_padded),
                headers=_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            recent = data.get("filings", {}).get("recent", {})
            accession_numbers = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            forms = recent.get("form", [])

            # Normalize accession_no for comparison
            target = accession_no.replace("-", "")

            for i, acc in enumerate(accession_numbers):
                if acc.replace("-", "") == target:
                    if i < len(primary_docs) and i < len(forms):
                        primary_doc = primary_docs[i]
                        return _ARCHIVES_BASE.format(
                            cik=cik,
                            accession_clean=target,
                            primary_doc=primary_doc,
                        )

        except Exception as e:
            logger.warning(f"[edgar] Failed to get document URL for CIK {cik}: {e}")

        # Fallback: return the filing index directory URL
        accession_clean = accession_no.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/"
