"""Tests for guardian.ai.ollama_client — generate, JSON extraction, model selection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── _extract_json (pure function — no mocking needed) ────────────────────────

def test_extract_json_raw():
    from guardian.ai.ollama_client import _extract_json

    result = _extract_json('{"key": "value", "num": 42}')
    assert result == {"key": "value", "num": 42}


def test_extract_json_fenced_block():
    from guardian.ai.ollama_client import _extract_json

    text = 'Here is the analysis:\n```json\n{"score": 0.9, "action": "restart"}\n```'
    result = _extract_json(text)
    assert result == {"score": 0.9, "action": "restart"}


def test_extract_json_embedded_in_prose():
    from guardian.ai.ollama_client import _extract_json

    text = 'The system is healthy. Output: {"health": "ok", "issues": []} -- end.'
    result = _extract_json(text)
    assert result is not None
    assert result["health"] == "ok"


def test_extract_json_invalid_returns_none():
    from guardian.ai.ollama_client import _extract_json

    assert _extract_json("no json here at all") is None
    assert _extract_json("") is None
    assert _extract_json("{broken json") is None


def test_extract_json_fenced_without_language_tag():
    from guardian.ai.ollama_client import _extract_json

    text = '```\n{"foo": "bar"}\n```'
    result = _extract_json(text)
    assert result == {"foo": "bar"}


# ── OllamaClient.is_available ─────────────────────────────────────────────────

async def test_is_available_returns_true():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    assert await client.is_available() is True


async def test_is_available_returns_false_on_error():
    from guardian.ai.ollama_client import OllamaClient
    import httpx

    client = OllamaClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await client.is_available() is False


async def test_is_available_returns_false_on_non_200():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 503
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    assert await client.is_available() is False


# ── OllamaClient.list_models ──────────────────────────────────────────────────

async def test_list_models_returns_names():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "models": [{"name": "llama3.2:3b"}, {"name": "gemma4:latest"}]
    }
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    models = await client.list_models()
    assert "llama3.2:3b" in models
    assert "gemma4:latest" in models


async def test_list_models_returns_empty_on_error():
    from guardian.ai.ollama_client import OllamaClient
    import httpx

    client = OllamaClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    models = await client.list_models()
    assert models == []


# ── OllamaClient.best_available_model ─────────────────────────────────────────

async def test_best_available_model_first_preference():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "models": [{"name": "llama3.2:3b"}, {"name": "llama3.1:8b"}]
    }
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    best = await client.best_available_model(["llama3.1:8b", "llama3.2:3b"])
    assert best == "llama3.1:8b"


async def test_best_available_model_fallback_to_any():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": [{"name": "gemma4:latest"}]}
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    best = await client.best_available_model(["llama3.1:8b"])
    assert best == "gemma4:latest"


async def test_best_available_model_none_when_no_models():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": []}
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_response)

    best = await client.best_available_model(["llama3.1:8b"])
    assert best is None


# ── OllamaClient.generate ─────────────────────────────────────────────────────

async def test_generate_returns_response_text():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "The system is healthy.", "eval_count": 12}
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.generate("Is the system healthy?", model="llama3.2:3b")
    assert result == "The system is healthy."


async def test_generate_raises_on_timeout():
    from guardian.ai.ollama_client import OllamaClient
    import httpx

    client = OllamaClient()
    client._client = AsyncMock()
    client._client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with pytest.raises(httpx.TimeoutException):
        await client.generate("test prompt", model="llama3.2:3b")


# ── OllamaClient.generate_json ────────────────────────────────────────────────

async def test_generate_json_parses_raw_json():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": '{"health_score": 0.8, "issues": []}',
        "eval_count": 20,
    }
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.generate_json("analyze", model="llama3.2:3b")
    assert result is not None
    assert result["health_score"] == pytest.approx(0.8)


async def test_generate_json_parses_fenced_block():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": 'Analysis:\n```json\n{"summary": "all good", "confidence": 0.95}\n```',
        "eval_count": 30,
    }
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.generate_json("analyze", model="llama3.2:3b")
    assert result["summary"] == "all good"


async def test_generate_json_returns_none_when_unparseable():
    from guardian.ai.ollama_client import OllamaClient

    client = OllamaClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "I cannot provide JSON right now.", "eval_count": 5}
    mock_response.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.generate_json("analyze", model="llama3.2:3b", retries=0)
    assert result is None
