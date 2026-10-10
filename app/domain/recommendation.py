from app.domain.analysis import GeneratedAnalysis, InvestmentHorizon, RecommendationAction
from app.domain.market_data import StockSnapshot
from app.domain.news import NewsAssessment


def apply_recommendation_policy(
    analysis: GeneratedAnalysis,
    *,
    owns_stock: bool,
    snapshot: StockSnapshot | None = None,
    horizon: InvestmentHorizon | None = None,
) -> GeneratedAnalysis:
    """Apply deterministic ownership and completeness guardrails."""

    result = analysis.model_copy(deep=True)

    if snapshot is not None:
        fundamentals = snapshot.fundamentals
        if fundamentals:
            for limitation in fundamentals.limitations:
                if limitation not in result.data_limitations:
                    result.data_limitations.append(limitation)
        for peer in snapshot.peer_fundamentals:
            if peer.status != "available":
                result.data_limitations.append(f"Peer {peer.symbol}: fundamentals are {peer.status}.")
        has_financials = fundamentals is not None and (
            bool(fundamentals.annual_financials)
            or any(metric.key == "revenue_ttm" for metric in fundamentals.metrics)
        )
        has_valuation = fundamentals is not None and any(
            metric.key in {"trailing_pe", "forward_pe", "price_to_sales", "ev_to_ebitda"}
            for metric in fundamentals.metrics
        )
        if not has_financials:
            result.fundamental_analysis = "Company financial evidence is unavailable. Revenue growth, profitability, and cash generation could not be assessed."
        if not has_valuation:
            result.valuation_assessment = "Valuation is inconclusive because usable valuation evidence is missing. Price trends do not establish whether shares are overpriced or underpriced."
        if not has_financials or not has_valuation:
            result.data_limitations.append("Financial or valuation evidence is incomplete; fundamental attractiveness is not established.")
            if horizon == InvestmentHorizon.LONG_TERM and result.action == RecommendationAction.BUY:
                result.action = RecommendationAction.WATCH
                result.confidence = min(result.confidence, 0.4)
                result.summary = "Watch: the available fundamental and valuation evidence is insufficient to support a long-term purchase."
                result.future_entry_plan.status = "Wait for financial and valuation evidence"
                result.future_entry_plan.conditions.insert(0, "Obtain current financial statements and usable valuation evidence before considering a long-term purchase.")

        _apply_news_policy(result, snapshot, horizon)

    if not owns_stock and result.action in {
        RecommendationAction.HOLD,
        RecommendationAction.SELL,
    }:
        original = result.action.value
        result.action = RecommendationAction.WATCH
        result.summary = (
            "Watch: the supplied evidence does not support a new purchase for a user "
            "without an existing position."
        )
        result.data_limitations.append(
            f"The model returned {original} for a user without a position; "
            "the application normalized this to WATCH."
        )

    if owns_stock and result.action == RecommendationAction.WATCH:
        reason = result.summary.removeprefix("Watch:").strip()
        result.action = RecommendationAction.HOLD
        result.confidence = min(result.confidence, 0.55)
        result.summary = (
            "Hold: maintain the existing position while awaiting clearer evidence."
            + (f" {reason}" if reason else "")
        )
        result.future_entry_plan.status = "Maintain position; wait before adding shares"
        result.data_limitations.append(
            "WATCH was normalized to HOLD for an existing position; this does not support adding shares."
        )
        if result.news_analysis and result.news_analysis.recommendation_impact.startswith(
            "The application changed the short-term BUY to WATCH"
        ):
            result.news_analysis.recommendation_impact = (
                "Current news could not be assessed reliably, so the application recommends "
                "HOLD rather than adding shares."
            )

    if result.action == RecommendationAction.WATCH and not result.future_entry_plan.conditions:
        result.future_entry_plan.conditions.append(
            "Wait for new market data that materially improves the risk/reward profile."
        )

    if not result.evidence:
        result.data_limitations.append(
            "The response did not cite snapshot evidence; treat the recommendation as low-confidence."
        )
        result.confidence = min(result.confidence, 0.35)

    if result.action != analysis.action:
        # A guardrail can replace the model's action. Keep the visible lead
        # consistent with the action the application actually saved.
        result.decision_factors = []

    return result


def _apply_news_policy(
    result: GeneratedAnalysis, snapshot: StockSnapshot, horizon: InvestmentHorizon | None,
) -> None:
    news = snapshot.news
    if news:
        for limitation in news.limitations:
            if limitation not in result.data_limitations:
                result.data_limitations.append(limitation)
    usable = bool(news and news.articles and news.status in {"available", "partial", "stale"})
    assessment_valid = result.news_analysis is not None
    if usable and result.news_analysis:
        article_ids = {article.id for article in news.articles}
        if any(not set(event.article_ids) <= article_ids for event in result.news_analysis.events):
            assessment_valid = False
            result.data_limitations.append("The model cited unknown news article IDs; its news assessment was discarded.")
    if not usable or not assessment_valid:
        result.news_analysis = NewsAssessment(
            sentiment="unknown",
            summary=("No usable recent news was supplied; current catalysts and event risks could not be assessed."
                     if not usable else "The model did not provide a usable, source-linked news assessment."),
            recommendation_impact="News does not support this recommendation. Confidence is reduced until current event risks can be reviewed.",
            events=[],
        )
    current = usable and news.status in {"available", "partial"} and assessment_valid
    if not current:
        result.confidence = min(result.confidence, 0.55)
        result.data_limitations.append("Current news could not be assessed reliably; analysis confidence is capped at 55%.")
        if news and news.status == "stale":
            result.news_analysis.sentiment = "unknown"
            result.news_analysis.recommendation_impact = "News coverage is stale. Current event risk is unknown, so confidence is reduced."
        if horizon == InvestmentHorizon.SHORT_TERM and result.action == RecommendationAction.BUY:
            result.action = RecommendationAction.WATCH
            result.summary = "Watch: current news and event risks could not be assessed reliably enough to support a short-term purchase."
            result.future_entry_plan.status = "Wait for current news and event-risk review"
            result.future_entry_plan.conditions.insert(0, "Review current company news and material event risks before considering a short-term purchase.")
            result.news_analysis.recommendation_impact = "The application changed the short-term BUY to WATCH because current news could not be assessed reliably."
