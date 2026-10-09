from app.domain.analysis import GeneratedAnalysis, InvestmentHorizon, RecommendationAction
from app.domain.market_data import StockSnapshot


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

    if not owns_stock and result.action in {
        RecommendationAction.HOLD,
        RecommendationAction.SELL,
    }:
        original = result.action.value
        result.action = RecommendationAction.WATCH
        result.data_limitations.append(
            f"The model returned {original} for a user without a position; "
            "the application normalized this to WATCH."
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

    return result
