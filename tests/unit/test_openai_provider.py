import json

import httpx
import pytest
from openai import OpenAI

from app.core.config import Settings
from app.core.errors import AnalysisProviderError, ConfigurationError
from app.domain.analysis import AnalysisRequest, RecommendationAction
from app.providers.llm import openai_provider
from tests.integration.test_app import FakeMarketProvider, make_client
from tests.unit.test_domain import make_analysis


def mock_openai(monkeypatch, handler):
    clients = []

    def factory(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        # Exercise SDK retries without the backoff delay in unit tests.
        client._calculate_retry_timeout = lambda *args: 0
        clients.append(client)
        return client

    monkeypatch.setattr(openai_provider, "OpenAI", factory)
    return clients


def response_json():
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "test-model", "status": "completed",
        "output": [{
            "id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "annotations": [],
                         "text": make_analysis(RecommendationAction.WATCH).model_dump_json()}],
        }],
    }


def analyze(engine):
    return engine.analyze(FakeMarketProvider().get_stock_snapshot("CEG"),
                          AnalysisRequest(symbol="CEG", model="test-model"))


def test_slow_generation_has_longer_read_budget_and_preserves_structured_output(monkeypatch):
    def handler(request):
        timeout = request.extensions["timeout"]
        assert timeout == {"connect": 10, "read": 180, "write": 30, "pool": 10}
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        # Simulate a response that would exceed the former 60-second read budget.
        if timeout["read"] < 120:
            raise httpx.ReadTimeout("generation still running", request=request)
        return httpx.Response(200, json=response_json())

    clients = mock_openai(monkeypatch, handler)
    result = analyze(openai_provider.OpenAIAnalysisEngine("test-key"))
    assert result.action == RecommendationAction.WATCH
    assert clients[0].is_closed()


def test_timeouts_retry_once_close_client_and_provide_recovery_instructions(monkeypatch, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("sensitive upstream error", request=request)

    clients = mock_openai(monkeypatch, handler)
    with pytest.raises(AnalysisProviderError) as error:
        analyze(openai_provider.OpenAIAnalysisEngine("test-key"))
    assert len(calls) == 2
    assert clients[0].is_closed()
    assert "CEG" in str(error.value)
    assert "180 seconds per attempt" in str(error.value)
    assert "OPENAI_TIMEOUT_SECONDS" in str(error.value)
    assert "sensitive upstream" not in str(error.value)
    assert "elapsed=" in caplog.text
    assert "test-key" not in caplog.text


def test_transient_timeout_recovers_on_retry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary", request=request)
        return httpx.Response(200, json=response_json())

    clients = mock_openai(monkeypatch, handler)
    assert analyze(openai_provider.OpenAIAnalysisEngine("test-key")).action == RecommendationAction.WATCH
    assert len(calls) == 2
    assert clients[0].is_closed()


def test_custom_timeout_and_retry_override_are_respected(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 300
        raise httpx.ReadTimeout("temporary", request=request)

    mock_openai(monkeypatch, handler)
    with pytest.raises(AnalysisProviderError, match="300 seconds per attempt"):
        analyze(openai_provider.OpenAIAnalysisEngine("test-key", timeout_seconds=300, max_retries=0))
    assert len(calls) == 1


def test_authentication_failure_is_not_retried(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "Invalid key"}})

    clients = mock_openai(monkeypatch, handler)
    with pytest.raises(ConfigurationError, match="rejected the API key"):
        analyze(openai_provider.OpenAIAnalysisEngine("test-key"))
    assert len(calls) == 1
    assert clients[0].is_closed()


def test_timeout_failure_is_saved_and_form_preserves_inputs(tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("temporary", request=request)

    mock_openai(monkeypatch, handler)
    with make_client(tmp_path, analysis_engine=openai_provider.OpenAIAnalysisEngine("test-key", max_retries=0)) as client:
        response = client.post("/analyze", data={
            "symbol": "CEG", "horizon": "short_term", "position": "not_owned", "model": "test-model",
        })
        assert response.status_code == 502
        assert "180 seconds per attempt" in response.text
        assert 'value="CEG"' in response.text
        saved = client.get("/api/v1/analyses").json()[0]
        assert saved["status"] == "failed"
        assert "OPENAI_TIMEOUT_SECONDS" in saved["error_message"]


def test_settings_default_and_environment_override(monkeypatch):
    monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)
    assert Settings(_env_file=None).openai_timeout_seconds == 180
    assert Settings(_env_file=None).openai_max_retries == 1
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")
    assert Settings(_env_file=None).openai_timeout_seconds == 300
    assert Settings(_env_file=None).openai_max_retries == 0
