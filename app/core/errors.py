class StockPilotError(Exception):
    """Base error safe to surface to the local UI."""


class ConfigurationError(StockPilotError):
    pass


class InvalidSymbolError(StockPilotError):
    pass


class MarketDataError(StockPilotError):
    pass


class AnalysisProviderError(StockPilotError):
    pass


class AnalysisNotFoundError(StockPilotError):
    pass

