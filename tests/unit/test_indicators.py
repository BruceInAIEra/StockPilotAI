import pytest

from app.services.indicators import (
    annualized_volatility,
    percent_return,
    relative_strength_index,
    simple_moving_average,
)


def test_simple_moving_average_uses_latest_period() -> None:
    assert simple_moving_average([1, 2, 3, 4, 5], 3) == 4
    assert simple_moving_average([1, 2], 3) is None


def test_percent_return() -> None:
    assert percent_return([100, 105, 110], 2) == 10
    assert percent_return([100], 1) is None


def test_rsi_bounds_and_direction() -> None:
    rising = [float(value) for value in range(1, 17)]
    falling = list(reversed(rising))
    assert relative_strength_index(rising) == 100
    assert relative_strength_index(falling) == 0


def test_volatility_is_non_negative() -> None:
    value = annualized_volatility([100, 101, 99, 103, 102])
    assert value is not None
    assert value >= 0


def test_invalid_periods_return_none() -> None:
    assert simple_moving_average([1, 2, 3], 0) is None
    assert percent_return([1, 2, 3], 0) is None
    assert relative_strength_index([1, 2, 3], 0) is None

