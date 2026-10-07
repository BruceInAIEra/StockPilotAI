from __future__ import annotations

import logging

import openai
from openai import OpenAI

from app.core.errors import AnalysisProviderError, ConfigurationError
from app.domain.analysis import AnalysisRequest, GeneratedAnalysis
from app.domain.market_data import StockSnapshot
from app.providers.llm.prompt import SYSTEM_PROMPT, build_analysis_input

logger = logging.getLogger(__name__)


class OpenAIAnalysisEngine:
    def __init__(self, api_key: str | None, timeout_seconds: float = 60) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def analyze(
        self,
        snapshot: StockSnapshot,
        request: AnalysisRequest,
    ) -> GeneratedAnalysis:
        if not self._api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not configured. Add it to your shell or local .env file."
            )

        client = OpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=2,
        )
        try:
            response = client.responses.parse(
                model=request.model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": build_analysis_input(snapshot, request),
                    },
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
            raise AnalysisProviderError("OpenAI timed out. Please try again.") from exc
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
        return parsed

