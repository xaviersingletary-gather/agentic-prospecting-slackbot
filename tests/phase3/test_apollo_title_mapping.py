"""Spec §1.3 — Apollo title-keyword mapping per persona.

Personas re-aligned to the Gather AI Knowledge Base buyer framework (4 personas):
  Technical Lead   → CI / Automation / Industrial Engineering directors
  Operations Lead  → VP Ops / Director of Warehouse / Inventory Control
  Executive        → CSCO / COO / SVP Ops / SVP Supply Chain
  Compliance Lead  → IT + Safety / EHS

Title keyword lists are intentionally tight (≤6 each) to keep Apollo's
`person_titles` filter under its 422 threshold. Tests assert that the
*highest-priority* keyword from each persona is present rather than
pinning the full list — the list will continue to be tuned over time.
"""

# One must-have keyword per persona — the title most likely to actually
# book a first meeting.
PRIORITY_KEYWORD = {
    "technical_lead": "Director of Continuous Improvement",
    "operations_lead": "VP Operations",
    "executive": "Chief Supply Chain Officer",
    "compliance_lead": "Director of IT",
}


def test_each_persona_has_priority_keyword():
    from src.research.personas import map_personas_to_title_keywords

    for key, expected in PRIORITY_KEYWORD.items():
        got = map_personas_to_title_keywords([key])
        assert expected in got, f"{key}: expected {expected!r} in {got}"


def test_persona_keyword_lists_stay_under_apollo_limit():
    """Apollo `person_titles` returns 422 once the array gets long.
    Cap each persona at 6 keywords; cap the union at 24."""
    from src.research.personas import PERSONAS, map_personas_to_title_keywords

    for key, cfg in PERSONAS.items():
        assert len(cfg["title_keywords"]) <= 6, (
            f"{key} has {len(cfg['title_keywords'])} keywords; Apollo "
            f"won't accept lists this long"
        )

    union = map_personas_to_title_keywords(list(PERSONAS.keys()))
    assert len(union) <= 24, f"union of all personas: {len(union)} keywords"


def test_keyword_list_is_deduplicated():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(["technical_lead", "technical_lead"])
    assert len(got) == len(set(got))


def test_empty_persona_list_returns_empty_keywords():
    from src.research.personas import map_personas_to_title_keywords

    assert map_personas_to_title_keywords([]) == []


def test_unknown_persona_key_is_silently_ignored():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(["executive", "totally_not_a_persona"])
    expected = set(map_personas_to_title_keywords(["executive"]))
    assert set(got) == expected
