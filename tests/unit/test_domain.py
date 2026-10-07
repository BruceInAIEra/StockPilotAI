import pytest
from pydantic import ValidationError

from app.domain.analysis import (
    AnalysisRequest,
    EvidenceItem,
    FutureEntryPlan,
    GeneratedAnalysis,
    InvestmentHorizon,
    RecommendationAction,
)
from app.domain.recommendation import apply_recommendation_policy


def make_analysis(action: RecommendationAction) -> GeneratedAnalysis:
    return GeneratedAnalysis(
        action=action,
        confidence=0.8,
        summary="A balanced snapshot review.",
        bull_case=["Price trend is positive."],
        bear_case=["Volatility is elevated."],
        risks=["Price-only data is incomplete."],
        evidence=[EvidenceItem(claim="Price is 100", metric="current_price", value="100")],
        future_entry_plan=FutureEntryPlan(
            status="Wait for confirmation",
            conditions=["Trend remains positive"],
            invalidation_conditions=["Trend reverses"],
        ),
        data_limitations=["No fundamentals"],
    )


def test_request_normalizes_symbol() -> None:
    request = AnalysisRequest(
        symbol=" brk.b ",
        horizon=InvestmentHorizon.LONG_TERM,
        owns_stock=False,
        model="gpt-5-mini",
    )
    assert request.symbol == "BRK.B"


def test_request_rejects_invalid_symbol() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            symbol="bad ticker!",
            horizon=InvestmentHorizon.MEDIUM_TERM,
            owns_stock=False,
            model="gpt-5-mini",
        )


@pytest.mark.parametrize(
    "action",
    [RecommendationAction.HOLD, RecommendationAction.SELL],
)
def test_non_owner_hold_or_sell_becomes_watch(action: RecommendationAction) -> None:
    result = apply_recommendation_policy(make_analysis(action), owns_stock=False)
    assert result.action == RecommendationAction.WATCH
    assert any("normalized" in item for item in result.data_limitations)


def test_owner_action_is_preserved() -> None:
    result = apply_recommendation_policy(
        make_analysis(RecommendationAction.HOLD), owns_stock=True
    )
    assert result.action == RecommendationAction.HOLD


def test_empty_evidence_caps_confidence() -> None:
    analysis = make_analysis(RecommendationAction.WATCH)
    analysis.evidence = []
    result = apply_recommendation_policy(analysis, owns_stock=False)
    assert result.confidence == 0.35

