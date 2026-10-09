from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.news import NewsArticle
from app.providers.market_data.news import YahooNewsProvider

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def story(title="Example raises guidance", days=1, url="https://example.com/guidance", **fields):
    return {"content": {
        "title": title, "pubDate": (NOW - timedelta(days=days)).isoformat(),
        "provider": {"displayName": "Example Wire"}, "canonicalUrl": {"url": url},
        "summary": "Management raised its full-year revenue guidance.", **fields,
    }}


def news_snapshot(rows=None):
    class Ticker:
        def get_news(self, **kwargs):
            assert kwargs == {"count": 40, "tab": "news"}
            return rows if rows is not None else [story()]
    return YahooNewsProvider(lambda symbol: Ticker(), clock=lambda: NOW,
                             search_fetcher=lambda symbol: []).get_news("TEST")


class FakeNewsProvider:
    def get_news(self, symbol):
        return news_snapshot().model_copy(update={"symbol": symbol})


def test_normalizes_sources_dates_and_deduplicates_before_model_input():
    snapshot = news_snapshot([
        story(days=2), story(url="https://example.com/guidance?tracking=1"),
        story(title="EXAMPLE RAISES GUIDANCE", url="https://other.example/story"),
        story("Older report", days=3, url="https://example.com/older"),
        story("Expired", days=31, url="https://example.com/expired"),
    ])
    assert snapshot.status == "available"
    assert len(snapshot.articles) == 2
    assert snapshot.articles[0].published_at == NOW - timedelta(days=1)
    assert snapshot.articles[0].publisher == "Example Wire"
    assert snapshot.window_start == NOW - timedelta(days=30)


@pytest.mark.parametrize("fields", [
    {"pubDate": "not a date"}, {"pubDate": "2026-10-09T10:00:00"},
    {"pubDate": (NOW + timedelta(days=1)).isoformat()},
    {"title": ""}, {"provider": {}},
    {"canonicalUrl": {"url": "javascript:alert(1)"}},
    {"canonicalUrl": {"url": "file:///etc/passwd"}},
])
def test_invalid_articles_are_excluded_and_partial_coverage_disclosed(fields):
    snapshot = news_snapshot([story(), story(**fields)])
    assert snapshot.status == "partial"
    assert len(snapshot.articles) == 1
    assert any("Excluded 1" in item for item in snapshot.limitations)


def test_legacy_response_and_headline_only_are_supported():
    snapshot = news_snapshot([{
        "title": "Legacy report", "publisher": "Wire", "link": "https://example.com/legacy",
        "providerPublishTime": (NOW - timedelta(days=2)).timestamp(),
    }])
    assert snapshot.status == "available"
    assert snapshot.articles[0].summary is None


@pytest.mark.parametrize("rows,status", [
    ([], "empty"), ([story(days=31)], "empty"),
    ([story(days=8)], "stale"), ([None, {"content": None}], "unavailable"),
    ({"unexpected": "schema"}, "unavailable"),
])
def test_empty_stale_and_malformed_coverage_are_distinct(rows, status):
    assert news_snapshot(rows).status == status


def test_outage_does_not_raise_or_expose_upstream_details():
    def broken(symbol):
        raise TimeoutError("private upstream message")
    snapshot = YahooNewsProvider(broken, clock=lambda: NOW, search_fetcher=broken).get_news("TEST")
    assert snapshot.status == "unavailable"
    assert "private" not in snapshot.model_dump_json()


def test_model_input_is_bounded_and_newest_first():
    rows = [story(f"Story {i}", days=i / 10, url=f"https://example.com/{i}", summary="a" * 4000)
            for i in range(40)]
    snapshot = news_snapshot(list(reversed(rows)))
    assert len(snapshot.articles) == 20
    assert snapshot.articles[0].title == "Story 0"
    assert len(snapshot.articles[0].summary) == 2000


def test_stored_news_rejects_unsafe_links():
    with pytest.raises(ValidationError):
        NewsArticle(id="1", title="Test", publisher="Wire", url="javascript:alert(1)", published_at=NOW)


@pytest.mark.parametrize("fails", [False, True])
def test_search_fallback_recovers_empty_or_failed_ticker_stream(fails):
    class Ticker:
        def get_news(self, **kwargs):
            if fails:
                raise TimeoutError()
            return []

    data = YahooNewsProvider(lambda symbol: Ticker(), clock=lambda: NOW,
                             search_fetcher=lambda symbol: [story()]).get_news("TEST")
    assert data.status == "available"
    assert len(data.articles) == 1
    assert "news search" in data.source
    assert any("fallback" in item for item in data.limitations)


def test_news_explicitly_tagged_to_other_tickers_is_excluded():
    data = news_snapshot([story(relatedTickers=["OTHER"])])
    assert data.status == "empty"
    assert data.articles == []
