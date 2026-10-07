from app.domain.analysis import GeneratedAnalysis, RecommendationAction


def apply_recommendation_policy(
    analysis: GeneratedAnalysis,
    *,
    owns_stock: bool,
) -> GeneratedAnalysis:
    """Apply deterministic ownership and completeness guardrails."""

    result = analysis.model_copy(deep=True)

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

