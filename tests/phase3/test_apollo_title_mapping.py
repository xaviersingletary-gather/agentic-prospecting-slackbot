"""Spec §1.3 — Apollo title-keyword mapping per persona.

CSCO                    → ["Chief Supply Chain", "CSCO", "SVP Supply Chain"]
VP Warehouse Ops        → ["VP Warehouse", "VP Operations", "Head of Warehouse",
                           "Director Warehouse Operations"]
VP Inventory & Planning → ["VP Inventory", "VP Planning", "VP S&OP", "Director Inventory"]
S&OP Lead               → ["S&OP", "Sales and Operations",
                           "Demand Planning Director", "Supply Planning"]
"""

EXPECTED = {
    "csco": ["Chief Supply Chain", "CSCO", "SVP Supply Chain"],
    "vp_warehouse_ops": [
        "VP Warehouse", "VP Operations", "Head of Warehouse",
        "Director Warehouse Operations",
    ],
    "vp_inventory_planning": [
        "VP Inventory", "VP Planning", "VP S&OP", "Director Inventory",
    ],
    "sop_lead": [
        "S&OP", "Sales and Operations", "Demand Planning Director", "Supply Planning",
    ],
}


def test_each_persona_maps_to_its_keyword_list():
    from src.research.personas import map_personas_to_title_keywords

    for key, expected in EXPECTED.items():
        got = map_personas_to_title_keywords([key])
        for kw in expected:
            assert kw in got, f"{key}: expected keyword {kw!r} in {got}"


def test_vp_warehouse_ops_keywords_match_spec_exactly():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(["vp_warehouse_ops"])
    assert set(got) == set(EXPECTED["vp_warehouse_ops"])


def test_all_four_personas_returns_union_of_keywords():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(list(EXPECTED.keys()))
    expected_union = {kw for kws in EXPECTED.values() for kw in kws}
    assert set(got) == expected_union


def test_keyword_list_is_deduplicated():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(["csco", "csco"])
    assert len(got) == len(set(got))


def test_empty_persona_list_returns_empty_keywords():
    from src.research.personas import map_personas_to_title_keywords

    assert map_personas_to_title_keywords([]) == []


def test_unknown_persona_key_is_silently_ignored():
    from src.research.personas import map_personas_to_title_keywords

    got = map_personas_to_title_keywords(["csco", "totally_not_a_persona"])
    assert set(got) == set(EXPECTED["csco"])
