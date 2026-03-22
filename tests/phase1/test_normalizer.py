"""
Phase 1 Tests — Input Normalizer Agent
All tests use mocked LLM and integration calls so no API keys are required.
"""
import pytest
from unittest.mock import MagicMock, patch
from src.agents.normalizer import InputNormalizerAgent, RepRequest, NormalizedRequest


@pytest.fixture
def agent():
    with patch("src.agents.normalizer.anthropic.Anthropic"), \
         patch("src.agents.normalizer.HubSpotClient"), \
         patch("src.agents.normalizer.ClayClient"):
        return InputNormalizerAgent()


def mock_extraction(agent, account_name=None, persona_filter=None, use_case_angle=None):
    agent._extract_intent = MagicMock(return_value={
        "account_name": account_name,
        "persona_filter": persona_filter,
        "use_case_angle": use_case_angle,
    })


def mock_hubspot(agent, result=None):
    agent.hubspot.find_company = MagicMock(return_value=result)


def mock_clay(agent, result=None):
    agent.clay.find_company = MagicMock(return_value=result)


# --- Account name extraction ---

def test_account_name_extracted(agent):
    mock_extraction(agent, account_name="Nestlé")
    mock_hubspot(agent, result={"domain": "nestle.com", "description": "Food company"})
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé", rep_id="U123"))
    assert result.account_name == "Nestlé"


def test_account_name_null_when_unclear(agent):
    mock_extraction(agent, account_name=None)
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="do the thing", rep_id="U123"))
    assert result.account_name == ""


# --- Domain enrichment ---

def test_domain_from_hubspot(agent):
    mock_extraction(agent, account_name="Nestlé")
    mock_hubspot(agent, result={"domain": "nestle.com", "description": "Food"})
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé", rep_id="U123"))
    assert result.account_domain == "nestle.com"


def test_domain_fallback_to_clay(agent):
    mock_extraction(agent, account_name="Nestlé")
    mock_hubspot(agent, result=None)
    mock_clay(agent, result={"domain": "nestle.com", "description": "Food"})
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé", rep_id="U123"))
    assert result.account_domain == "nestle.com"


def test_domain_empty_when_not_found(agent):
    mock_extraction(agent, account_name="UnknownCorp")
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="Run outreach for UnknownCorp", rep_id="U123"))
    assert result.account_domain == ""


# --- Persona filter extraction ---

def test_persona_filter_extracted(agent):
    mock_extraction(agent, account_name="Nestlé", persona_filter=["TDM", "FS"])
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé targeting VP ops", rep_id="U123"))
    assert result.persona_filter == ["TDM", "FS"]


def test_invalid_persona_types_filtered(agent):
    mock_extraction(agent, account_name="Nestlé", persona_filter=["TDM", "INVALID", "FS"])
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="...", rep_id="U123"))
    assert "INVALID" not in result.persona_filter
    assert "TDM" in result.persona_filter


def test_persona_filter_none_when_not_mentioned(agent):
    mock_extraction(agent, account_name="Nestlé", persona_filter=None)
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé", rep_id="U123"))
    assert result.persona_filter is None


# --- Use case angle ---

def test_use_case_angle_extracted(agent):
    mock_extraction(agent, account_name="Nestlé", use_case_angle="food safety compliance")
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="Nestlé food safety angle", rep_id="U123"))
    assert result.use_case_angle == "food safety compliance"


# --- Confidence scoring ---

def test_high_confidence_with_domain_and_context(agent):
    mock_extraction(agent, account_name="Nestlé", persona_filter=["TDM"], use_case_angle="compliance")
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="...", rep_id="U123"))
    assert result.confidence >= 0.7
    assert result.clarification_needed is False


def test_low_confidence_no_account(agent):
    mock_extraction(agent, account_name=None)
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="do the thing", rep_id="U123"))
    assert result.confidence < 0.7
    assert result.clarification_needed is True
    assert result.clarification_question is not None


def test_medium_confidence_account_only(agent):
    mock_extraction(agent, account_name="Nestlé")
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="Run outreach for Nestlé", rep_id="U123"))
    assert result.confidence == 0.6
    assert result.clarification_needed is True


# --- Rep role ---

def test_rep_role_preserved(agent):
    mock_extraction(agent, account_name="Nestlé")
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="...", rep_id="U123", rep_role="MDR"))
    assert result.rep_role == "MDR"


# --- Clarification questions ---

def test_clarification_question_no_account(agent):
    mock_extraction(agent, account_name=None)
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="help me with that company", rep_id="U123"))
    assert "company" in result.clarification_question.lower() or "which" in result.clarification_question.lower()


def test_clarification_question_with_account(agent):
    mock_extraction(agent, account_name="Nestle")
    mock_hubspot(agent, result=None)
    mock_clay(agent, result=None)
    result = agent.normalize(RepRequest(raw_message="Nestle outreach", rep_id="U123"))
    assert "Nestle" in result.clarification_question


# --- to_dict ---

def test_normalized_request_to_dict(agent):
    mock_extraction(agent, account_name="Nestlé", persona_filter=["TDM"])
    mock_hubspot(agent, result={"domain": "nestle.com"})
    result = agent.normalize(RepRequest(raw_message="...", rep_id="U123"))
    d = result.to_dict()
    assert "account_name" in d
    assert "confidence" in d
    assert "clarification_needed" in d
