"""Spec §1.3 — Apollo title-keyword mapping per persona.

Personas re-aligned to the Gather AI Knowledge Base buyer framework (4 personas):
  Technical Lead   → CI / Automation / Industrial Engineering directors
  Operations Lead  → VP Ops / GM Warehouse / Inventory Control / ICQA
  Executive        → CSCO / COO / SVP Ops / SVP Supply Chain
  Compliance Lead  → IT (Infrastructure / Security / WMS Systems) + Safety / EHS
"""

EXPECTED = {
    "technical_lead": [
        "Continuous Improvement",
        "Industrial Engineer",
        "Automation Manager",
        "VP Engineering",
        "Director of Process Improvement",
    ],
    "operations_lead": [
        "VP Operations",
        "Director of Warehouse",
        "GM Warehouse",
        "Inventory Control Manager",
        "Director of ICQA",
    ],
    "executive": [
        "Chief Supply Chain",
        "CSCO",
        "Chief Operating Officer",
        "SVP Operations",
        "SVP Supply Chain",
    ],
    "compliance_lead": [
        "VP IT",
        "Director of EHS",
        "VP Risk Management",
        "Corporate Safety Director",
    ],
}


def test_each_persona_maps_to_its_keyword_list():
    from src.research.personas import map_personas_to_title_keywords

    for key, expected in EXPECTED.items():
        got = map_personas_to_title_keywords([key])
        for kw in expected:
            assert kw in got, f"{key}: expected keyword {kw!r} in {got}"


def test_operations_lead_keywords_include_warehouse_and_icqa():
    from src.research.personas import map_personas_to_title_keywords

    got = set(map_personas_to_title_keywords(["operations_lead"]))
    assert "VP Operations" in got
    assert "Director of Warehouse" in got
    assert "Director of ICQA" in got


def test_all_four_personas_returns_union_of_keywords():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(list(EXPECTED.keys()))
    expected_union = {kw for kws in EXPECTED.values() for kw in kws}
    assert expected_union.issubset(set(got))


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
