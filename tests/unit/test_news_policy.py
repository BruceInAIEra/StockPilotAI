import pytest

from app.domain.analysis import AnalysisRequest, InvestmentHorizon, RecommendationAction
from app.domain.recommendation import apply_recommendation_policy
from tests.integration.test_app import FakeAnalysisEngine, FakeMarketProvider
from tests.unit.test_fundamentals import snapshot as fundamentals_snapshot
from tests.unit.test_news import news_snapshot, story


def inputs(rows=None):
    snapshot = FakeMarketProvider().get_stock_snapshot("TEST")
    snapshot.fundamentals = fundamentals_snapshot()
    snapshot.news = news_snapshot(rows)
    generated = FakeAnalysisEngine().analyze(snapshot, AnalysisRequest(symbol="TEST", model="test"))
    return snapshot, generated


@pytest.mark.parametrize("rows", [[], [story(days=9)], [{"content": None}]])
def test_missing_or_stale_news_is_unknown_and_blocks_short_term_buy(rows):
    snapshot, generated = inputs(rows)
    result = apply_recommendation_policy(generated, owns_stock=False, snapshot=snapshot,
                                         horizon=InvestmentHorizon.SHORT_TERM)
    assert result.action == RecommendationAction.WATCH
    assert result.confidence <= .55
    assert result.news_analysis.sentiment == "unknown"
    assert "news" in result.future_entry_plan.conditions[0]


def test_missing_news_does_not_force_exit_or_remove_supported_long_term_buy():
    snapshot, generated = inputs([])
    for action, owns_stock in [(RecommendationAction.BUY, False), (RecommendationAction.HOLD, True),
                               (RecommendationAction.SELL, True)]:
        generated.action = action
        result = apply_recommendation_policy(generated, owns_stock=owns_stock, snapshot=snapshot,
                                             horizon=InvestmentHorizon.LONG_TERM)
        assert result.action == action
        assert result.confidence <= .55
        assert result.news_analysis.sentiment == "unknown"


def test_unknown_source_ids_discard_news_assessment_and_reduce_confidence():
    snapshot, generated = inputs()
    generated.news_analysis.events[0].article_ids = ["invented-id"]
    result = apply_recommendation_policy(generated, owns_stock=False, snapshot=snapshot,
                                         horizon=InvestmentHorizon.SHORT_TERM)
    assert result.action == RecommendationAction.WATCH
    assert result.news_analysis.events == []
    assert result.news_analysis.sentiment == "unknown"
    assert any("unknown news article IDs" in item for item in result.data_limitations)
    assert generated.news_analysis.events  # policy does not mutate the original


def test_omitted_news_assessment_is_not_treated_as_completed_review():
    snapshot, generated = inputs()
    generated.news_analysis = None
    result = apply_recommendation_policy(generated, owns_stock=False, snapshot=snapshot,
                                         horizon=InvestmentHorizon.SHORT_TERM)
    assert result.action == RecommendationAction.WATCH
    assert "did not provide" in result.news_analysis.summary


def test_valid_news_preserves_model_action_and_event_evidence():
    snapshot, generated = inputs()
    result = apply_recommendation_policy(generated, owns_stock=False, snapshot=snapshot,
                                         horizon=InvestmentHorizon.SHORT_TERM)
    assert result.action == RecommendationAction.BUY
    assert result.confidence == generated.confidence
    assert result.news_analysis == generated.news_analysis
    assert all(item in result.data_limitations for item in snapshot.news.limitations)
