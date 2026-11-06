from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.dependencies import AppContainer
from app.api.routes import admin, api, auth, health, pages
from app.api.auth import AccountAccessMiddleware, validate_csrf
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
from app.services.auth_service import AuthService
from app.services.admin_service import AdminService, ModelsUnavailable


def create_app(
    settings: Settings | None = None,
    market_provider: MarketDataProvider | None = None,
    analysis_engine: AnalysisEngine | None = None,
    fundamentals_provider: FundamentalsProvider | None = None,
    news_provider: NewsProvider | None = None,
    auth_provider=None,
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
    admin_service = AdminService(session_factory, settings)
    analysis_service = AnalysisService(
        market_data=configured_market_provider,
        analysis_engine=configured_analysis_engine,
        repository=repository,
        allowed_models=settings.allowed_models,
        model_settings_provider=admin_service.model_settings,
        fundamentals_provider=fundamentals_provider or YahooFundamentalsProvider(),
        news_provider=news_provider or YahooNewsProvider(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Path("data").mkdir(exist_ok=True)
        engine = session_factory.kw["bind"]
        try:
            # Only the local SQLite workflow creates its schema automatically.
            # PostgreSQL and production deployments must run Alembic first.
            if (settings.app_env in {"development", "test"}
                    and settings.database_url.startswith("sqlite")):
                Base.metadata.create_all(engine)
            yield
        finally:
            try:
                close = getattr(configured_market_provider, "close", None)
                if close:
                    close()
            finally:
                engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(validate_csrf)],
    )
    application.state.container = AppContainer(
        settings=settings,
        admin_service=admin_service,
        analysis_service=analysis_service,
        auth_service=AuthService(session_factory, settings, auth_provider),
    )
    application.state.session_factory = session_factory

    @application.exception_handler(ModelsUnavailable)
    async def unavailable_models(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        # Never echo passwords, tokens, or other submitted values in error bodies.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ]})
    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(admin.router)
    application.include_router(api.router)
    application.include_router(pages.router)
    application.add_middleware(AccountAccessMiddleware, auth_service=application.state.container.auth_service)
    return application


app = create_app()
