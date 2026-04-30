"""
Phase 2 tests — Persona Discovery + Checkpoint 1 UI

Run with: pytest tests/phase2/ -v
"""
import pytest
from unittest.mock import patch, MagicMock

from unittest.mock import patch as _patch

from src.agents.discovery import (
    classify_persona_type,
    classify_seniority,
    assign_lane,
    normalize_apollo_person,
    PersonaDiscoveryAgent,
)
from src.integrations.slack_blocks import persona_list_card, persona_card
import src.agents.discovery as _discovery_module


def _no_mock(fn):
    """Decorator: force MOCK_PERSONAS=False so integration tests exercise the real Apollo path."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _patch.object(_discovery_module.settings, "MOCK_PERSONAS", False):
            return fn(*args, **kwargs)

    return wrapper


# --- Unit: persona type classification ---

class TestPersonaTypeClassification:
    def test_tdm_titles(self):
        assert classify_persona_type("Director of Continuous Improvement") == "TDM"
        assert classify_persona_type("Automation Manager") == "TDM"
        assert classify_persona_type("Industrial Engineer") == "TDM"

    def test_odm_titles(self):
        assert classify_persona_type("VP of Operations") == "ODM"
        assert classify_persona_type("Director of Warehouse Operations") == "ODM"
        assert classify_persona_type("Director of ICQA") == "ODM"
        assert classify_persona_type("Inventory Control Manager") == "ODM"

    def test_fs_titles(self):
        assert classify_persona_type("COO") == "FS"
        assert classify_persona_type("Chief Operating Officer") == "FS"
        assert classify_persona_type("SVP Operations") == "FS"
        assert classify_persona_type("VP Finance") == "FS"

    def test_it_titles(self):
        assert classify_persona_type("VP IT") == "IT"
        assert classify_persona_type("Director of Information Technology") == "IT"

    def test_safety_titles(self):
        assert classify_persona_type("EHS Manager") == "Safety"
        assert classify_persona_type("Director of Safety") == "Safety"

    def test_unknown_falls_back_to_odm(self):
        assert classify_persona_type("Random Title XYZ") == "ODM"


# --- Unit: seniority classification ---

class TestSeniorityClassification:
    def test_c_suite(self):
        assert classify_seniority("COO") == "C-Suite"
        assert classify_seniority("Chief Supply Chain Officer") == "C-Suite"
        assert classify_seniority("EVP Operations") == "C-Suite"

    def test_svp(self):
        assert classify_seniority("SVP Operations") == "SVP"
        assert classify_seniority("Senior Vice President, Supply Chain") == "SVP"

    def test_vp(self):
        assert classify_seniority("VP of Operations") == "VP"
        assert classify_seniority("Vice President, Warehouse") == "VP"

    def test_director(self):
        assert classify_seniority("Director of CI") == "Director"
        assert classify_seniority("Director of Fulfillment") == "Director"

    def test_manager(self):
        assert classify_seniority("Manager, Inventory Control") == "Manager"
        assert classify_seniority("Lead Engineer") == "Manager"


# --- Unit: lane assignment ---

class TestLaneAssignment:
    def test_vp_plus_goes_to_ae(self):
        assert assign_lane("C-Suite") == "AE"
        assert assign_lane("SVP") == "AE"
        assert assign_lane("VP") == "AE"

    def test_director_goes_to_mdr(self):
        assert assign_lane("Director") == "MDR"
        assert assign_lane("Manager") == "MDR"
        assert assign_lane("IC") == "MDR"


# --- Unit: normalize_apollo_person ---

class TestNormalizeApolloPerson:
    def _raw_person(self, title="Director of CI", first="Jane", last="Smith"):
        return {
            "id": "apollo-123",
            "first_name": first,
            "last_name": last,
            "title": title,
            "email": "jane@example.com",
            "linkedin_url": "https://linkedin.com/in/janesmith",
            "organization": {"name": "Acme Corp"},
        }

    def test_basic_fields_populated(self):
        result = normalize_apollo_person(self._raw_person(), session_id="sess-1")
        assert result["first_name"] == "Jane"
        assert result["last_name"] == "Smith"
        assert result["title"] == "Director of CI"
        assert result["email"] == "jane@example.com"
        assert result["session_id"] == "sess-1"
        assert result["apollo_id"] == "apollo-123"

    def test_c_suite_always_high_priority(self):
        result = normalize_apollo_person(self._raw_person(title="COO"), session_id="sess-1")
        assert result["priority_score"] == "High"
        assert result["outreach_lane"] == "AE"

    def test_director_gets_correct_tier_and_lane(self):
        result = normalize_apollo_person(self._raw_person(title="Director of Operations"), session_id="s")
        assert result["seniority"] == "Director"
        assert result["outreach_lane"] == "MDR"

    def test_default_tier_fs_is_high(self):
        result = normalize_apollo_person(self._raw_person(title="VP Finance"), session_id="s")
        assert result["persona_type"] == "FS"
        assert result["priority_score"] == "High"

    def test_default_tier_safety_is_low(self):
        result = normalize_apollo_person(self._raw_person(title="EHS Manager"), session_id="s")
        assert result["persona_type"] == "Safety"
        assert result["priority_score"] == "Low"


# --- Integration: PersonaDiscoveryAgent ---

class TestPersonaDiscoveryAgent:
    def _mock_apollo_results(self, count=10):
        titles = [
            "COO", "SVP Operations", "VP Operations", "Director of CI",
            "Director of Warehouse", "VP IT", "Director of ICQA",
            "EHS Manager", "VP Finance", "Manager of Inventory",
        ]
        return [
            {
                "id": f"apollo-{i}",
                "first_name": f"First{i}",
                "last_name": f"Last{i}",
                "title": titles[i % len(titles)],
                "email": f"person{i}@acme.com",
                "linkedin_url": f"https://linkedin.com/in/person{i}",
                "organization": {"name": "Acme Corp"},
            }
            for i in range(count)
        ]

    @_no_mock
    @patch("src.agents.discovery.ClayClient")
    @patch("src.agents.discovery.ApolloClient")
    def test_max_8_enforced(self, MockApollo, MockClay):
        mock_apollo = MockApollo.return_value
        mock_apollo.search_people.return_value = self._mock_apollo_results(20)
        mock_clay = MockClay.return_value
        mock_clay.get_linkedin_signals.return_value = []

        agent = PersonaDiscoveryAgent()
        agent.apollo = mock_apollo
        agent.clay = mock_clay

        results = agent.discover("sess-1", "Acme Corp")
        assert len(results) <= 8

    @_no_mock
    @patch("src.agents.discovery.ClayClient")
    @patch("src.agents.discovery.ApolloClient")
    def test_empty_apollo_returns_empty(self, MockApollo, MockClay):
        mock_apollo = MockApollo.return_value
        mock_apollo.search_people.return_value = []
        agent = PersonaDiscoveryAgent()
        agent.apollo = mock_apollo
        agent.clay = MockClay.return_value

        results = agent.discover("sess-1", "Unknown Corp")
        assert results == []

    @_no_mock
    @patch("src.agents.discovery.ClayClient")
    @patch("src.agents.discovery.ApolloClient")
    def test_persona_filter_applied(self, MockApollo, MockClay):
        mock_apollo = MockApollo.return_value
        mock_apollo.search_people.return_value = self._mock_apollo_results(10)
        mock_clay = MockClay.return_value
        mock_clay.get_linkedin_signals.return_value = []

        agent = PersonaDiscoveryAgent()
        agent.apollo = mock_apollo
        agent.clay = mock_clay

        results = agent.discover("sess-1", "Acme Corp", persona_filter=["TDM"])
        for p in results:
            assert p["persona_type"] == "TDM"

    @_no_mock
    @patch("src.agents.discovery.ClayClient")
    @patch("src.agents.discovery.ApolloClient")
    def test_sorted_high_to_low(self, MockApollo, MockClay):
        mock_apollo = MockApollo.return_value
        mock_apollo.search_people.return_value = self._mock_apollo_results(10)
        mock_clay = MockClay.return_value
        mock_clay.get_linkedin_signals.return_value = []

        agent = PersonaDiscoveryAgent()
        agent.apollo = mock_apollo
        agent.clay = mock_clay

        results = agent.discover("sess-1", "Acme Corp")
        tier_order = {"High": 0, "Medium": 1, "Low": 2}
        tiers = [tier_order[p["priority_score"]] for p in results]
        assert tiers == sorted(tiers)

    @_no_mock
    @patch("src.agents.discovery.ClayClient")
    @patch("src.agents.discovery.ApolloClient")
    def test_clay_failure_does_not_crash(self, MockApollo, MockClay):
        mock_apollo = MockApollo.return_value
        mock_apollo.search_people.return_value = self._mock_apollo_results(3)
        mock_clay = MockClay.return_value
        mock_clay.get_linkedin_signals.side_effect = Exception("Clay timeout")

        agent = PersonaDiscoveryAgent()
        agent.apollo = mock_apollo
        agent.clay = mock_clay

        results = agent.discover("sess-1", "Acme Corp")
        assert len(results) > 0  # still returns personas even if Clay fails
        for p in results:
            assert p["linkedin_signals"] == []


# --- Unit: Slack UI blocks ---

class TestPersonaCardBlocks:
    def _sample_persona(self, persona_id="p-1"):
        return {
            "id": persona_id,
            "first_name": "Jane",
            "last_name": "Smith",
            "title": "Director of CI",
            "persona_type": "TDM",
            "seniority": "Director",
            "outreach_lane": "MDR",
            "priority_score": "High",
            "score_reasoning": None,
            "value_driver": None,
            "linkedin_signals": [],
        }

    def test_persona_card_renders(self):
        block = persona_card(self._sample_persona(), index=0)
        assert block["type"] == "section"
        assert "Jane Smith" in block["text"]["text"]
        assert "Director of CI" in block["text"]["text"]

    def test_persona_list_card_renders_all(self):
        personas = [self._sample_persona(f"p-{i}") for i in range(5)]
        blocks = persona_list_card(personas, "sess-1")
        # Should have header + divider + 5 persona cards + divider + confirm button
        assert len(blocks) >= 7
        # Confirm button present
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert any(
            any(e.get("action_id") == "confirm_personas" for e in b.get("elements", []))
            for b in action_blocks
        )

    def test_confirm_button_has_session_id(self):
        blocks = persona_list_card([self._sample_persona()], "sess-abc")
        action_block = next(
            b for b in blocks
            if b.get("type") == "actions" and
            any(e.get("action_id") == "confirm_personas" for e in b.get("elements", []))
        )
        confirm_btn = next(e for e in action_block["elements"] if e["action_id"] == "confirm_personas")
        assert confirm_btn["value"] == "sess-abc"
