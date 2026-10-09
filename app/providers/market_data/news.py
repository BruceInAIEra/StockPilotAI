from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

from app.domain.news import NewsArticle, NewsSnapshot

logger = logging.getLogger(__name__)
LOOKBACK_DAYS = 30
FRESH_DAYS = 7
MAX_ARTICLES = 20


class NewsProvider(Protocol):
    def get_news(self, symbol: str) -> NewsSnapshot: ...


def unavailable_news(symbol: str, now: datetime | None = None) -> NewsSnapshot:
    now = now or datetime.now(UTC)
    return NewsSnapshot(
        symbol=symbol, status="unavailable", retrieved_at=now,
        window_start=now - timedelta(days=LOOKBACK_DAYS),
        limitations=["News could not be retrieved; recent catalysts and event risks are unknown."],
    )


def published_time(value: object) -> datetime:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    raise ValueError("Missing or ambiguous publication time")


def clean_text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


class YahooNewsProvider:
    """A bounded, timestamped sample of ticker-associated headlines and summaries."""

    def __init__(self, ticker_factory: Callable | None = None,
                 clock: Callable[[], datetime] | None = None,
                 search_fetcher: Callable[[str], list] | None = None) -> None:
        self._ticker_factory = ticker_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._search_fetcher = search_fetcher

    def get_news(self, symbol: str) -> NewsSnapshot:
        now = self._clock()
        result = unavailable_news(symbol, now)
        raw = None
        try:
            factory = self._ticker_factory
            if factory is None:
                import yfinance as yf

                yf.set_tz_cache_location(str(Path("data/yfinance-cache").resolve()))
                factory = yf.Ticker
            raw = factory(symbol).get_news(count=40, tab="news")
            if not isinstance(raw, list):
                raise ValueError("Invalid news response")
        except Exception:
            logger.warning("Ticker news unavailable for %s", symbol)
            raw = None

        # Yahoo's ticker stream can silently return an empty list. Its search
        # endpoint offers a second, bounded route to publisher-linked news.
        used_search = False
        if not raw:
            try:
                if self._search_fetcher:
                    search_rows = self._search_fetcher(symbol)
                else:
                    import yfinance as yf

                    search_rows = yf.Search(symbol, max_results=0, news_count=40,
                                            lists_count=0, recommended=0, timeout=15).news
                if not isinstance(search_rows, list):
                    raise ValueError("Invalid search news response")
                if raw is None and not search_rows:
                    return result
                raw = search_rows
                used_search = True
            except Exception:
                logger.warning("News search unavailable for %s", symbol)
                return result

        result.limitations = [
            "Limited ticker-associated news sample, not comprehensive company, industry, or macro coverage. Relevance must be assessed.",
            "Only supplied headlines and summaries were reviewed, not full articles or independent verification. Reports may be incomplete or disputed.",
            "Publication time may differ from event time. News sentiment does not establish price impact or whether an event is already priced in.",
        ]
        if used_search:
            result.source = "Yahoo Finance news search via yfinance"
            result.limitations.append("The ticker news stream was empty or unavailable; news search was used as a fallback.")
        candidates = []
        skipped = 0
        for row in raw:
            try:
                if not isinstance(row, dict):
                    raise ValueError("Invalid article")
                if row.get("ad"):
                    continue
                content = row.get("content", row)
                if not isinstance(content, dict):
                    raise ValueError("Invalid article content")
                related = content.get("relatedTickers")
                if isinstance(related, list) and related and symbol.upper() not in {
                    value.upper() for value in related if isinstance(value, str)
                }:
                    continue
                published = published_time(content.get("pubDate") or content.get("providerPublishTime"))
                if published > now:
                    raise ValueError("Future publication time")
                if published < result.window_start:
                    continue
                title = clean_text(content.get("title"), 500)
                provider = content.get("provider")
                publisher = clean_text(provider.get("displayName") if isinstance(provider, dict)
                                       else content.get("publisher"), 200)
                canonical = content.get("canonicalUrl")
                clickthrough = content.get("clickThroughUrl")
                url = (canonical.get("url") if isinstance(canonical, dict) else None) or (
                    clickthrough.get("url") if isinstance(clickthrough, dict) else None
                ) or content.get("link")
                if not title or not publisher or not isinstance(url, str):
                    raise ValueError("Missing source metadata")
                # Validate before deriving a stable ID; query parameters do not identify evidence.
                article = NewsArticle(id="pending", title=title, publisher=publisher, url=url,
                                      published_at=published,
                                      summary=clean_text(content.get("summary"), 2000) or None)
                parts = urlsplit(article.url)
                identity = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                article.id = hashlib.sha256(identity.encode()).hexdigest()[:16]
                candidates.append(article)
            except (ValueError, TypeError, OverflowError, OSError):
                skipped += 1

        seen_ids, seen_titles = set(), set()
        for article in sorted(candidates, key=lambda item: item.published_at, reverse=True):
            title_key = article.title.casefold()
            if article.id in seen_ids or title_key in seen_titles:
                continue
            seen_ids.add(article.id)
            seen_titles.add(title_key)
            result.articles.append(article)
            if len(result.articles) == MAX_ARTICLES:
                break
        if skipped:
            result.limitations.append(f"Excluded {skipped} news items with invalid or missing source metadata or publication times.")
        if not result.articles:
            result.status = "unavailable" if skipped else "empty"
            result.limitations.append("No usable news in the last 30 days was returned. This is not evidence of neutral sentiment or absence of material events.")
        elif result.articles[0].published_at < now - timedelta(days=FRESH_DAYS):
            result.status = "stale"
            result.limitations.append("The newest usable news is more than 7 days old; current event risk is unknown.")
        else:
            result.status = "partial" if skipped else "available"
        return result
