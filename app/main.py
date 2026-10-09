from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import AppContainer
from app.api.routes import api, health, pages
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.providers.llm.base import AnalysisEngine
from app.providers.llm.openai_provider import OpenAIAnalysisEngine
from app.providers.market_data.base import MarketDataProvider
from app.providers.market_data.yahoo import YahooFinanceProvider
from app.providers.market_data.fundamentals import FundamentalsProvider, YahooFundamentalsProvider
from app.providers.market_data.news import NewsProvider, YahooNewsProvider
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.database import Base, create_session_factory
from app.services.analysis_service import AnalysisService


def create_app(
    settings: Settings | None = None,
    market_provider: MarketDataProvider | None = None,
    analysis_engine: AnalysisEngine | None = None,
    fundamentals_provider: FundamentalsProvider | None = None,
    news_provider: NewsProvider | None = None,
) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()
    session_factory = create_session_factory(settings.database_url)
    repository = AnalysisRepository(session_factory)
    configured_market_provider = market_provider or YahooFinanceProvider(
        settings.market_data_timeout_seconds
    )
    configured_analysis_engine = analysis_engine or OpenAIAnalysisEngine(
        api_key=(
            settings.openai_api_key.get_secret_value()
            if settings.openai_api_key
            else None
        ),
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    analysis_service = AnalysisService(
        market_data=configured_market_provider,
        analysis_engine=configured_analysis_engine,
        repository=repository,
        allowed_models=settings.allowed_models,
        fundamentals_provider=fundamentals_provider or YahooFundamentalsProvider(),
        news_provider=news_provider or YahooNewsProvider(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Path("data").mkdir(exist_ok=True)
        engine = session_factory.kw["bind"]
        Base.metadata.create_all(engine)
        yield
        close = getattr(configured_market_provider, "close", None)
        if close:
            close()
        engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.container = AppContainer(
        settings=settings,
        analysis_service=analysis_service,
    )
    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(health.router)
    application.include_router(api.router)
    application.include_router(pages.router)
    return application


app = create_app()
