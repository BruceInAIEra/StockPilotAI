from __future__ import annotations

import math
import statistics


def simple_moving_average(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 4)


def percent_return(values: list[float], periods_back: int) -> float | None:
    if periods_back <= 0 or len(values) <= periods_back:
        return None
    start = values[-(periods_back + 1)]
    if start == 0:
        return None
    return round(((values[-1] / start) - 1) * 100, 4)


def relative_strength_index(values: list[float], period: int = 14) -> float | None:
    if period <= 0 or len(values) <= period:
        return None

    recent = values[-(period + 1) :]
    changes = [current - previous for previous, current in zip(recent, recent[1:])]
    gains = [max(change, 0) for change in changes]
    losses = [abs(min(change, 0)) for change in changes]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100.0
    if average_gain == 0:
        return 0.0

    relative_strength = average_gain / average_loss
    return round(100 - (100 / (1 + relative_strength)), 4)


def annualized_volatility(values: list[float]) -> float | None:
    if len(values) < 3:
        return None

    log_returns = [
        math.log(current / previous)
        for previous, current in zip(values, values[1:])
        if previous > 0 and current > 0
    ]
    if len(log_returns) < 2:
        return None
    return round(statistics.stdev(log_returns) * math.sqrt(252) * 100, 4)

