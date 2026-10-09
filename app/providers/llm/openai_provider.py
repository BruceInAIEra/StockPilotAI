from __future__ import annotations

import logging
from time import monotonic

import httpx
import openai
from openai import OpenAI

from app.core.errors import AnalysisProviderError, ConfigurationError
from app.domain.analysis import AnalysisRequest, GeneratedAnalysis
from app.domain.market_data import StockSnapshot
from app.providers.llm.prompt import SYSTEM_PROMPT, build_analysis_input

logger = logging.getLogger(__name__)


class OpenAIAnalysisEngine:
    def __init__(self, api_key: str | None, timeout_seconds: float = 180,
                 max_retries: int = 1) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def analyze(
        self,
        snapshot: StockSnapshot,
        request: AnalysisRequest,
    ) -> GeneratedAnalysis:
        if not self._api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not configured. Add it to your shell or local .env file."
            )

        analysis_input = build_analysis_input(snapshot, request)
        started = monotonic()
        logger.info(
            "Starting OpenAI analysis symbol=%s model=%s input_chars=%s read_timeout=%ss max_retries=%s",
            request.symbol, request.model, len(analysis_input),
            self._timeout_seconds, self._max_retries,
        )
        try:
            # Give generation time to finish while failing connection problems
            # promptly. Always close the HTTP client, including on timeout.
            with OpenAI(
                api_key=self._api_key,
                timeout=httpx.Timeout(
                    self._timeout_seconds,
                    connect=min(10, self._timeout_seconds),
                    write=min(30, self._timeout_seconds),
                    pool=min(10, self._timeout_seconds),
                ),
                max_retries=self._max_retries,
            ) as client:
                response = client.responses.parse(
                    model=request.model,
                    input=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": analysis_input},
                    ],
                    text_format=GeneratedAnalysis,
                )
        except openai.AuthenticationError as exc:
            raise ConfigurationError(
                "OpenAI rejected the API key. Check OPENAI_API_KEY and try again."
            ) from exc
        except openai.RateLimitError as exc:
            raise AnalysisProviderError(
                "OpenAI rate or usage limit reached. Wait briefly or check account usage."
            ) from exc
        except openai.APITimeoutError as exc:
            logger.warning(
                "OpenAI analysis timed out symbol=%s model=%s elapsed=%.1fs read_timeout=%ss max_retries=%s",
                request.symbol, request.model, monotonic() - started,
                self._timeout_seconds, self._max_retries,
            )
            raise AnalysisProviderError(
                f"OpenAI timed out while analyzing {request.symbol} with {request.model} "
                f"(read timeout: {self._timeout_seconds:g} seconds per attempt). "
                "Try again or choose another model. If this persists, increase "
                "OPENAI_TIMEOUT_SECONDS in .env and restart the app."
            ) from exc
        except openai.APIConnectionError as exc:
            raise AnalysisProviderError(
                "Could not reach OpenAI. Check your internet connection."
            ) from exc
        except openai.APIStatusError as exc:
            logger.warning(
                "OpenAI request failed status=%s request_id=%s",
                exc.status_code,
                exc.request_id,
            )
            if exc.status_code == 404:
                raise AnalysisProviderError(
                    f"Model '{request.model}' is unavailable for this API key. "
                    "Choose another configured model."
                ) from exc
            raise AnalysisProviderError(
                f"OpenAI returned an error (status {exc.status_code}). Please try again."
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected OpenAI analysis failure")
            raise AnalysisProviderError(
                "The model response could not be processed. Please try again."
            ) from exc

        parsed = response.output_parsed
        if parsed is None:
            raise AnalysisProviderError(
                "The model did not return a usable analysis. Try another model or try again."
            )
        logger.info(
            "OpenAI analysis completed symbol=%s model=%s elapsed=%.1fs request_id=%s",
            request.symbol, request.model, monotonic() - started,
            getattr(response, "_request_id", None),
        )
        return parsed
