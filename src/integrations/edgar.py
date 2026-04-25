"""
SEC EDGAR integration — finds a company's latest 10-K or 20-F filing.
Uses the EDGAR EFTS entity-search API (no API key required).

Note: Foreign companies (e.g. Lenovo on HKEX, Nestle on SIX) do not file
10-K or 20-F with the SEC, so this will return None for them. The researcher
falls back to Exa web research in that case.
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
    """Lightweight EDGAR client for locating annual SEC filings."""

    def find_latest_10k(self, company_name: str) -> Optional[dict]:
        """
        Search EDGAR for the company's latest annual filing.
        Tries 10-K first (US domestic), then 20-F (foreign private issuers).
        Returns dict with keys: entity_name, file_date, period, cik, accession_no,
        document_url, form_type. Returns None if no SEC filing exists.
        """
        # Try 10-K (US companies)
        hit, form_type = self._efts_search(company_name, "10-K")

        # Try 20-F (foreign private issuers like Nestle, Samsung, etc.)
        if not hit:
            hit, form_type = self._efts_search(company_name, "20-F")

        if not hit:
            logger.info(
                f"[edgar] '{company_name}' has no 10-K or 20-F — likely non-SEC filer "
                "(e.g. foreign company listed on HKEX, SIX, etc.)"
            )
            return None

        accession_no = hit.get("accession_no", "")
        if not accession_no:
            return None

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
            "form_type": form_type,
        }

    def _efts_search(self, company_name: str, form: str) -> tuple[Optional[dict], str]:
        """
        Search EFTS for the most recent filing of the given form type.
        Uses the `entity` param to match filer name (not full-text mentions),
        with a fallback to `q` text search for tickers / short names like "GM".
        Returns (hit_source_dict, form_type) or (None, form_type).
        """
        for search_params in [
            # Pass 1: entity-name match (filer IS the company)
            {"entity": company_name, "forms": form,
             "dateRange": "custom", "startdt": "2021-01-01", "enddt": "2026-12-31"},
            # Pass 2: full-text match (catches ticker symbols like "GM" that appear in filing text)
            {"q": f'"{company_name}"', "forms": form,
             "dateRange": "custom", "startdt": "2021-01-01", "enddt": "2026-12-31"},
        ]:
            try:
                resp = httpx.get(_EFTS_URL, params=search_params, headers=_HEADERS, timeout=10)
                resp.raise_for_status()
                hits = resp.json().get("hits", {}).get("hits", [])
                if not hits:
                    continue

                # Sort newest first
                hits.sort(
                    key=lambda h: h.get("_source", {}).get("file_date", ""), reverse=True
                )

                # For the text-search pass, verify the top hit's entity is actually our company
                # (text search can return other companies' filings that merely mention our target)
                source = hits[0].get("_source", {})
                entity = (source.get("entity_name") or "").upper()
                name_up = company_name.upper()
                if "entity" not in search_params:
                    # Full-text pass: skip if top hit entity looks unrelated
                    if name_up not in entity and entity not in name_up:
                        logger.info(
                            f"[edgar] Full-text hit entity '{entity}' doesn't match '{company_name}' — skipping"
                        )
                        continue

                logger.info(f"[edgar] Found {form} for '{company_name}': entity='{entity}'")
                return source, form
            except Exception as e:
                logger.warning(f"[edgar] EFTS search ({form}) failed for '{company_name}': {e}")

        return None, form

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
