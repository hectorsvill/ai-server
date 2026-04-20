"""
Async Ollama HTTP client.
Wraps the /api/generate and /api/chat endpoints with timeout and retry logic.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from guardian.core.config import cfg
from guardian.core.logger import get_logger

log = get_logger(__name__)

_BASE_URL = cfg.ai.ollama_url
_TIMEOUT = cfg.ai.timeout_seconds


class OllamaClient:
    """Lightweight async Ollama client.  Reuses a single httpx.AsyncClient."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=_TIMEOUT, write=30.0, pool=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── Health ────────────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            log.error("ollama_list_models_failed", error=str(e))
            return []

    async def best_available_model(self, preferred: list[str]) -> str | None:
        """Return the first model from preferred list that is actually loaded."""
        available = await self.list_models()
        for model in preferred:
            # Partial match: "llama3.2:3b" matches "llama3.2:3b" in the list
            for av in available:
                if model in av or av in model:
                    return av
        # Fallback: return whatever is available
        return available[0] if available else None

    # ── Generate ──────────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        stream: bool = False,
    ) -> str:
        """
        Send a generation request.  Returns the full response string.
        Uses the /api/generate endpoint (single-turn).
        """
        model = model or cfg.ai.model
        max_tokens = max_tokens or cfg.ai.max_tokens

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "top_p": 0.9,
            },
        }
        if system:
            payload["system"] = system

        try:
            log.debug("ollama_request", model=model, prompt_len=len(prompt))
            resp = await self._client.post("/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "")
            log.debug(
                "ollama_response",
                model=model,
                tokens=data.get("eval_count", 0),
                response_len=len(response_text),
            )
            return response_text
        except httpx.TimeoutException:
            log.error("ollama_timeout", model=model)
            raise
        except httpx.HTTPStatusError as e:
            log.error("ollama_http_error", status=e.response.status_code, model=model)
            raise
        except Exception as e:
            log.error("ollama_error", error=str(e), model=model)
            raise

    async def generate_json(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.1,
        retries: int = 2,
    ) -> dict[str, Any] | None:
        """
        Like generate() but expects a JSON object in the response.
        Extracts the first valid JSON block, with retry on parse failure.
        """
        for attempt in range(retries + 1):
            try:
                raw = await self.generate(
                    prompt=prompt,
                    model=model,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                # Extract JSON from the response (may be wrapped in markdown fences)
                extracted = _extract_json(raw)
                if extracted is not None:
                    return extracted
                log.warning("ollama_json_parse_failed", attempt=attempt, raw=raw[:200])
            except Exception as e:
                log.warning("ollama_generate_json_error", attempt=attempt, error=str(e))
                if attempt == retries:
                    raise
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """
    Try to extract a JSON object from an LLM response.
    Handles:
      - Raw JSON
      - ```json ... ``` fenced blocks
      - JSON embedded in prose
    """
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try fenced block
    import re
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find first { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# Module-level singleton
_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
