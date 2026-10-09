from datetime import date

import pytest

from app.providers.market_data.fundamentals import YahooFundamentalsProvider


class FakeTicker:
    def get_info(self):
        return {
            "longName": "Example Company", "quoteType": "EQUITY",
            "financialCurrency": "USD", "currency": "USD",
            "sector": "Technology", "industry": "Software",
            "marketCap": 10000, "totalRevenue": 1300,
            "grossMargins": .6, "profitMargins": .1,
            "trailingPE": 20, "trailingEps": 2, "forwardPE": 18,
            "priceToSalesTrailing12Months": 4, "enterpriseToEbitda": 12,
        }

    def get_income_stmt(self, **kwargs):
        return {
            "2025-12-31": {"TotalRevenue": 1200, "NetIncome": 150, "OperatingIncome": 240},
            "2024-12-31": {"TotalRevenue": 1000, "NetIncome": 100, "OperatingIncome": 150},
            "2023-12-31": {"TotalRevenue": 0, "NetIncome": -100},
        }

    def get_cash_flow(self, **kwargs):
        return {
            "2025-12-31": {"OperatingCashFlow": 200, "CapitalExpenditure": -50},
            "2024-12-31": {"FreeCashFlow": -20},
        }

    def get_balance_sheet(self, **kwargs):
        return {"2026-06-30": {
            "TotalDebt": 500, "CashCashEquivalentsAndShortTermInvestments": 200,
            "CurrentAssets": 800, "CurrentLiabilities": 400,
        }}


def snapshot(ticker=None):
    return YahooFundamentalsProvider(lambda symbol: ticker or FakeTicker()).get_fundamentals("TEST")


def test_financial_calculations_preserve_periods_and_currency():
    data = snapshot()
    assert data.status == "available"
    latest = data.annual_financials[0]
    assert latest.period_end == date(2025, 12, 31)
    assert latest.revenue_growth_percent == pytest.approx(20)
    assert latest.operating_margin_percent == 20
    assert latest.free_cash_flow == 150
    assert data.annual_financials[1].free_cash_flow == -20
    assert data.annual_financials[1].revenue_growth_percent is None
    metrics = {item.key: item for item in data.metrics}
    assert metrics["net_debt"].value == 300
    assert metrics["current_ratio"].value == 2
    assert metrics["gross_margin_percent"].value == 60
    assert metrics["annual_free_cash_flow"].unit == "USD"
    assert "2025-12-31" in metrics["annual_free_cash_flow"].period
    assert data.latest_statement_date == date(2026, 6, 30)


def test_nonfinite_numbers_negative_pe_and_missing_data_are_not_zero():
    class SparseTicker(FakeTicker):
        def get_info(self):
            return {**super().get_info(), "totalRevenue": float("nan"),
                    "trailingPE": -4, "forwardPE": float("inf"), "profitMargins": None}

        def get_cash_flow(self, **kwargs):
            raise TimeoutError()

    data = snapshot(SparseTicker())
    assert data.status == "partial"
    keys = {item.key for item in data.metrics}
    assert not keys & {"trailing_pe", "forward_pe", "revenue_ttm", "net_margin_percent"}
    assert data.annual_financials[0].free_cash_flow is None
    assert any("cash flow statements unavailable" in item for item in data.limitations)
    assert "NaN" not in data.model_dump_json()


def test_non_equity_does_not_show_company_ratios():
    class FundTicker(FakeTicker):
        def get_info(self):
            return {**super().get_info(), "quoteType": "ETF"}

    data = snapshot(FundTicker())
    assert data.status == "unavailable"
    assert data.metrics == []
    assert data.annual_financials == []


def test_outage_returns_explicit_unavailable_result():
    data = snapshot(object())
    assert data.status == "unavailable"
    assert data.metrics == []
    assert any("No usable company fundamentals" in item for item in data.limitations)


def test_revenue_growth_does_not_compare_across_missing_years():
    class GapTicker(FakeTicker):
        def get_income_stmt(self, **kwargs):
            return {"2025-12-31": {"TotalRevenue": 150}, "2023-12-31": {"TotalRevenue": 100}}

    assert snapshot(GapTicker()).annual_financials[0].revenue_growth_percent is None


def test_cash_flow_is_not_borrowed_from_another_fiscal_year():
    class MisalignedTicker(FakeTicker):
        def get_cash_flow(self, **kwargs):
            return {"2025-09-30": {"FreeCashFlow": 400}}

    assert snapshot(MisalignedTicker()).annual_financials[0].free_cash_flow is None


def test_stale_statements_are_flagged():
    class OldTicker(FakeTicker):
        def get_income_stmt(self, **kwargs):
            return {"2020-12-31": {"TotalRevenue": 100}}

    assert any("18 months old" in item for item in snapshot(OldTicker()).limitations)


def test_balance_sheet_falls_back_to_annual_and_preserves_zero_cash():
    class AnnualBalanceTicker(FakeTicker):
        def get_balance_sheet(self, **kwargs):
            if kwargs.get("freq") == "quarterly":
                return {}
            return {"2025-12-31": {"TotalDebt": 50, "CashAndCashEquivalents": 0}}

    metrics = {metric.key: metric for metric in snapshot(AnnualBalanceTicker()).metrics}
    assert metrics["net_debt"].value == 50
    assert metrics["cash_and_investments"].value == 0
    assert "2025-12-31" in metrics["total_debt"].period


def test_unknown_currency_is_not_silently_assumed_usd():
    class UnknownCurrencyTicker(FakeTicker):
        def get_info(self):
            return {**super().get_info(), "financialCurrency": None}

    data = snapshot(UnknownCurrencyTicker())
    assert data.financial_currency is None
    assert next(metric for metric in data.metrics if metric.key == "revenue_ttm").unit == "Currency unreported"


def test_positive_multiple_is_omitted_when_earnings_are_negative():
    class LossTicker(FakeTicker):
        def get_info(self):
            return {**super().get_info(), "trailingEps": -2}

    assert "trailing_pe" not in {metric.key for metric in snapshot(LossTicker()).metrics}
