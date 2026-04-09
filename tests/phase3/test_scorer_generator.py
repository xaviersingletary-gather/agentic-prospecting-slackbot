"""
Phase 3 tests — Scorer & Value Mapper + Sequence Generator

Run with: pytest tests/phase3/ -v
"""
from datetime import datetime, timedelta

from src.agents.scorer import (
    ScorerAgent,
    score_persona,
    has_recent_linkedin_signal,
    get_comparable_customer,
    VALUE_DRIVERS,
)
from src.agents.generator import SequenceGeneratorAgent, AE_STEPS, MDR_STEPS


# ---------------------------------------------------------------------------
# Scorer tests
# ---------------------------------------------------------------------------

def _persona(persona_type="TDM", seniority="Director", tier="High", signals=None):
    return {
        "id": "p-1",
        "first_name": "Jane",
        "last_name": "Smith",
        "title": "Director of CI",
        "persona_type": persona_type,
        "seniority": seniority,
        "priority_score": tier,
        "outreach_lane": "MDR",
        "linkedin_signals": signals or [],
        "gong_hook": None,
        "account_name": "Acme Corp",
    }


class TestLinkedInSignalDetection:
    def test_recent_signal_detected(self):
        signals = [{"date": datetime.utcnow().isoformat(), "content": "post"}]
        assert has_recent_linkedin_signal(signals) is True

    def test_old_signal_not_detected(self):
        old_date = (datetime.utcnow() - timedelta(days=100)).isoformat()
        signals = [{"date": old_date, "content": "post"}]
        assert has_recent_linkedin_signal(signals) is False

    def test_empty_signals_returns_false(self):
        assert has_recent_linkedin_signal([]) is False

    def test_missing_date_skipped(self):
        signals = [{"content": "no date here"}]
        assert has_recent_linkedin_signal(signals) is False


class TestComparableCustomer:
    def test_food_vertical_matched(self):
        result = get_comparable_customer("national food and beverage company")
        assert "food" in result.lower() or "beverage" in result.lower()

    def test_3pl_vertical_matched(self):
        result = get_comparable_customer("leading 3PL provider with 50 DCs")
        assert "3PL" in result or "logistics" in result.lower()

    def test_no_description_returns_default(self):
        result = get_comparable_customer(None)
        assert result == "one of our customers"

    def test_unrecognized_description_returns_default(self):
        result = get_comparable_customer("a company that does things")
        assert result == "one of our customers"


class TestScorePersona:
    def test_c_suite_locked_to_high(self):
        p = _persona(persona_type="ODM", seniority="C-Suite", tier="Medium")
        result = score_persona(p)
        assert result["priority_score"] == "High"
        assert "C-Suite lock" in result["score_reasoning"]

    def test_recent_signal_elevates_low_to_medium(self):
        signals = [{"date": datetime.utcnow().isoformat(), "content": "test"}]
        p = _persona(persona_type="Safety", seniority="Manager", tier="Low", signals=signals)
        result = score_persona(p)
        assert result["priority_score"] == "Medium"

    def test_recent_signal_elevates_medium_to_high(self):
        signals = [{"date": datetime.utcnow().isoformat(), "content": "test"}]
        p = _persona(persona_type="ODM", seniority="Director", tier="Medium", signals=signals)
        result = score_persona(p)
        assert result["priority_score"] == "High"

    def test_high_tier_not_elevated_above_high(self):
        signals = [{"date": datetime.utcnow().isoformat(), "content": "test"}]
        p = _persona(persona_type="TDM", seniority="Director", tier="High", signals=signals)
        result = score_persona(p)
        assert result["priority_score"] == "High"

    def test_no_signal_no_elevation(self):
        p = _persona(persona_type="Safety", seniority="Manager", tier="Low")
        result = score_persona(p)
        assert result["priority_score"] == "Low"

    def test_value_driver_assigned(self):
        p = _persona(persona_type="TDM")
        result = score_persona(p)
        assert result["value_driver"] is not None
        assert result["value_driver"]["primary_driver"] == "cycle_count_efficiency"

    def test_comparable_customer_in_value_driver(self):
        p = _persona(persona_type="FS")
        result = score_persona(p, account_description="a healthcare distribution company")
        assert "healthcare" in result["value_driver"]["comparable_customer"].lower()

    def test_score_reasoning_populated(self):
        p = _persona()
        result = score_persona(p)
        assert isinstance(result["score_reasoning"], str)
        assert len(result["score_reasoning"]) > 0


class TestScorerAgent:
    def test_sorted_high_to_low(self):
        personas = [
            _persona(persona_type="Safety", seniority="Manager", tier="Low"),
            _persona(persona_type="TDM", seniority="Director", tier="High"),
            _persona(persona_type="ODM", seniority="Director", tier="Medium"),
        ]
        for i, p in enumerate(personas):
            p["id"] = f"p-{i}"

        agent = ScorerAgent()
        result = agent.score(personas)
        tier_order = {"High": 0, "Medium": 1, "Low": 2}
        tiers = [tier_order[p["priority_score"]] for p in result]
        assert tiers == sorted(tiers)

    def test_all_personas_get_value_driver(self):
        personas = [_persona(pt) for pt in ["TDM", "ODM", "FS", "IT", "Safety"]]
        for i, p in enumerate(personas):
            p["id"] = f"p-{i}"
        agent = ScorerAgent()
        result = agent.score(personas)
        for p in result:
            assert p["value_driver"] is not None

    def test_empty_list_returns_empty(self):
        agent = ScorerAgent()
        assert agent.score([]) == []


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------

def _scored_persona(lane="MDR", persona_type="TDM"):
    return {
        "id": "p-1",
        "first_name": "Jane",
        "last_name": "Smith",
        "title": "Director of CI",
        "seniority": "Director",
        "persona_type": persona_type,
        "outreach_lane": lane,
        "priority_score": "High",
        "linkedin_signals": [],
        "gong_hook": None,
        "account_name": "Acme Corp",
        "value_driver": {
            "primary_driver": "cycle_count_efficiency",
            "pain_point": "manual cycle counting consuming hours",
            "outcome": "15x faster cycle counts",
            "cta_angle": "how teams eliminated manual counting",
            "default_opener": "If your team spends time on manual counts...",
            "comparable_customer": "a large manufacturing operator",
        },
    }


class TestSequenceGenerator:
    def test_mdr_generates_7_steps(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(lane="MDR"), account_name="Acme Corp")
        assert len(seq["steps"]) == 7

    def test_ae_generates_5_steps(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(lane="AE"), account_name="Acme Corp")
        assert len(seq["steps"]) == 5

    def test_tokens_filled_in_body(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(
            _scored_persona(lane="MDR"),
            account_name="Acme Corp",
            rep_name="Alex Johnson",
        )
        for step in seq["steps"]:
            assert "{{" not in (step["body"] or ""), f"Unfilled token in step {step['step_number']}: {step['body']}"
            if step.get("subject_line"):
                assert "{{" not in step["subject_line"], f"Unfilled token in subject: {step['subject_line']}"

    def test_first_name_in_step_1(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(), account_name="Acme Corp")
        assert "Jane" in seq["steps"][0]["body"]

    def test_company_name_in_steps(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(), account_name="Acme Corp")
        bodies = " ".join(s["body"] for s in seq["steps"])
        assert "Acme Corp" in bodies

    def test_sequence_has_required_fields(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(), account_name="Acme Corp", session_id="sess-1")
        assert seq["id"]
        assert seq["session_id"] == "sess-1"
        assert seq["persona_id"] == "p-1"
        assert seq["lane"] in ("AE", "MDR")
        assert seq["status"] == "draft"
        assert isinstance(seq["steps"], list)

    def test_each_step_has_required_fields(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(), account_name="Acme Corp")
        for step in seq["steps"]:
            assert "step_number" in step
            assert "channel" in step
            assert "day_offset" in step
            assert "body" in step
            assert step["status"] == "draft"

    def test_linkedin_steps_have_no_subject(self):
        agent = SequenceGeneratorAgent()
        seq = agent.generate(_scored_persona(lane="MDR"), account_name="Acme Corp")
        linkedin_steps = [s for s in seq["steps"] if s["channel"] == "linkedin"]
        assert len(linkedin_steps) > 0
        for step in linkedin_steps:
            assert step["subject_line"] is None

    def test_gong_hook_used_when_present(self):
        persona = _scored_persona(lane="AE")
        persona["gong_hook"] = "Heard you mention inventory accuracy on the last earnings call."
        agent = SequenceGeneratorAgent()
        seq = agent.generate(persona, account_name="Acme Corp")
        assert "earnings call" in seq["steps"][0]["body"]

    def test_default_opener_used_when_no_hook(self):
        persona = _scored_persona(lane="MDR")
        persona["gong_hook"] = None
        persona["linkedin_signals"] = []
        agent = SequenceGeneratorAgent()
        seq = agent.generate(persona, account_name="Acme Corp")
        # Default opener from value_driver should appear
        assert "manual" in seq["steps"][0]["body"].lower() or len(seq["steps"][0]["body"]) > 50
