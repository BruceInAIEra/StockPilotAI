from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote

from app.domain.fundamentals import (
    AnnualFinancials,
    FundamentalMetric,
    FundamentalsSnapshot,
)

logger = logging.getLogger(__name__)


class FundamentalsProvider(Protocol):
    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot: ...


def unavailable_fundamentals(symbol: str) -> FundamentalsSnapshot:
    return FundamentalsSnapshot(
        symbol=symbol,
        retrieved_at=datetime.now(UTC),
        source_url=f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/financials/",
        limitations=["Company fundamentals could not be retrieved. Valuation is inconclusive."],
    )


def number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def statement_rows(table: dict) -> dict[date, dict]:
    rows = {}
    for timestamp, values in table.items():
        try:
            period = date.fromisoformat(str(timestamp)[:10])
        except ValueError:
            continue
        if isinstance(values, dict):
            rows[period] = values
    return rows


class YahooFundamentalsProvider:
    """Normalize Yahoo's financial data; partial failures never erase price data."""

    def __init__(self, ticker_factory: Callable | None = None) -> None:
        self._ticker_factory = ticker_factory

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        result = unavailable_fundamentals(symbol)
        result.limitations = [
            "Yahoo financial data is vendor-normalized, not independently verified against filings.",
            "Retrieval time is not a filing date. Historical valuation multiples and news are not supplied.",
            "Forward P/E uses estimates; a low multiple alone does not establish undervaluation.",
        ]
        try:
            factory = self._ticker_factory
            if factory is None:
                import yfinance as yf

                yf.set_tz_cache_location(str(Path("data/yfinance-cache").resolve()))
                factory = yf.Ticker
            ticker = factory(symbol)
        except Exception:
            logger.warning("Could not initialize fundamentals for %s", symbol)
            return unavailable_fundamentals(symbol)

        def fetch(label: str, method: str, **kwargs) -> dict:
            try:
                value = getattr(ticker, method)(**kwargs)
                if not isinstance(value, dict) or not value:
                    raise ValueError("Empty response")
                return value
            except Exception:
                logger.warning("Fundamentals section unavailable: %s / %s", symbol, label)
                result.limitations.append(f"{label} unavailable for {symbol}.")
                return {}

        info = fetch("Company summary", "get_info")
        result.company_name = info.get("longName") or info.get("shortName")
        result.sector = info.get("sector")
        result.industry = info.get("industry")
        result.financial_currency = info.get("financialCurrency")
        if info.get("quoteType") not in (None, "EQUITY"):
            result.limitations.append("Company financial statements do not apply to this instrument.")
            return result

        income = statement_rows(fetch("Annual income statements", "get_income_stmt", as_dict=True))
        cash = statement_rows(fetch("Annual cash flow statements", "get_cash_flow", as_dict=True))
        balance = statement_rows(fetch("Quarterly balance sheets", "get_balance_sheet", as_dict=True, freq="quarterly"))
        if not balance:
            balance = statement_rows(fetch("Annual balance sheets", "get_balance_sheet", as_dict=True))

        for period in sorted(income, reverse=True)[:4]:
            values = income[period]
            revenue = number(values.get("TotalRevenue"))
            net_income = number(values.get("NetIncome"))
            operating_income = number(values.get("OperatingIncome"))
            cash_values = cash.get(period, {})
            fcf = number(cash_values.get("FreeCashFlow"))
            if fcf is None:
                operating_cash = number(cash_values.get("OperatingCashFlow"))
                capex = number(cash_values.get("CapitalExpenditure"))
                if operating_cash is not None and capex is not None:
                    fcf = operating_cash - abs(capex)
            if all(value is None for value in (revenue, net_income, operating_income, fcf)):
                continue
            previous = next((prior for prior in sorted(income, reverse=True) if 330 <= (period - prior).days <= 400), None)
            prior_revenue = number(income[previous].get("TotalRevenue")) if previous else None
            result.annual_financials.append(AnnualFinancials(
                period_end=period,
                revenue=revenue,
                revenue_growth_percent=(100 * (revenue / prior_revenue - 1)
                                        if revenue is not None and prior_revenue is not None and prior_revenue > 0 else None),
                net_income=net_income,
                operating_margin_percent=(100 * operating_income / revenue
                                          if operating_income is not None and revenue is not None and revenue > 0 else None),
                free_cash_flow=fcf,
            ))

        def add(key: str, label: str, value: object, unit: str, period: str,
                *, positive: bool = False, percent: bool = False) -> None:
            parsed = number(value)
            if parsed is None or (positive and parsed <= 0):
                return
            result.metrics.append(FundamentalMetric(
                key=key, label=label, value=parsed * 100 if percent else parsed,
                unit=unit, period=period,
            ))

        currency = result.financial_currency or "Currency unreported"
        quote_currency = info.get("currency") or "Currency unreported"
        as_of = f"Vendor snapshot retrieved {result.retrieved_at.date()}"
        add("market_cap", "Market capitalization", info.get("marketCap"), quote_currency, as_of, positive=True)
        add("revenue_ttm", "Revenue (TTM)", info.get("totalRevenue"), currency, "Trailing 12 months; period end not supplied")
        add("gross_margin_percent", "Gross margin (TTM)", info.get("grossMargins"), "%", "Trailing 12 months; period end not supplied", percent=True)
        add("net_margin_percent", "Net margin (TTM)", info.get("profitMargins"), "%", "Trailing 12 months; period end not supplied", percent=True)
        for key, label, field, period in (
            ("trailing_pe", "Trailing P/E", "trailingPE", "TTM earnings"),
            ("forward_pe", "Forward P/E (estimate)", "forwardPE", "Estimated future earnings; estimate period unreported"),
            ("price_to_sales", "Price / sales", "priceToSalesTrailing12Months", "TTM revenue"),
            ("ev_to_ebitda", "EV / EBITDA", "enterpriseToEbitda", "Vendor EBITDA; period unreported"),
        ):
            # Losses make an earnings multiple unsuitable for cheap/expensive comparisons.
            earnings = number(info.get("trailingEps"))
            if key == "trailing_pe" and earnings is not None and earnings <= 0:
                continue
            add(key, label, info.get(field), "×", f"{as_of}; {period}", positive=True)

        if balance:
            period = max(balance)
            values = balance[period]
            debt = number(values.get("TotalDebt"))
            cash_balance = number(values.get("CashCashEquivalentsAndShortTermInvestments"))
            if cash_balance is None:
                cash_balance = number(values.get("CashAndCashEquivalents"))
            add("total_debt", "Total debt", debt, currency, f"Balance sheet at {period}")
            add("cash_and_investments", "Cash and short-term investments", cash_balance, currency, f"Balance sheet at {period}")
            if debt is not None and cash_balance is not None:
                add("net_debt", "Net debt (debt less cash)", debt - cash_balance, currency, f"Balance sheet at {period}")
            assets = number(values.get("CurrentAssets"))
            liabilities = number(values.get("CurrentLiabilities"))
            if assets is not None and liabilities is not None and liabilities > 0:
                add("current_ratio", "Current ratio", assets / liabilities, "×", f"Balance sheet at {period}")

        if result.annual_financials:
            latest = result.annual_financials[0]
            annual_period = f"Fiscal year ended {latest.period_end}"
            add("annual_revenue_growth_percent", "Annual revenue growth (YoY)", latest.revenue_growth_percent, "%", annual_period)
            add("annual_operating_margin_percent", "Annual operating margin", latest.operating_margin_percent, "%", annual_period)
            add("annual_free_cash_flow", "Annual free cash flow", latest.free_cash_flow, currency, annual_period)

        dates = [row.period_end for row in result.annual_financials] + list(balance)
        result.latest_statement_date = max(dates) if dates else None
        today = result.retrieved_at.date()
        if result.annual_financials and (today - result.annual_financials[0].period_end).days > 550:
            result.limitations.append("Annual financial statements are more than 18 months old; treat growth and valuation context as stale.")
        if balance and (today - max(balance)).days > 200:
            result.limitations.append("The latest balance sheet is more than 200 days old.")
        if not result.financial_currency:
            result.limitations.append("Financial reporting currency is unreported; do not compare monetary amounts across companies.")
        keys = {metric.key for metric in result.metrics}
        if not {"trailing_pe", "forward_pe", "price_to_sales", "ev_to_ebitda"} & keys:
            result.limitations.append("Usable valuation multiples are unavailable or not meaningful (for example, negative earnings).")
        if result.metrics or result.annual_financials:
            result.status = "available" if income and cash and balance and info else "partial"
        else:
            result.limitations.append("No usable company fundamentals were supplied; valuation is inconclusive.")
        return result
