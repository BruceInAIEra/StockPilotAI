from typing import Protocol

from app.domain.market_data import StockSnapshot


class MarketDataProvider(Protocol):
    def get_stock_snapshot(self, symbol: str) -> StockSnapshot: ...

