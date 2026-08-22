"""
Root Cause Agent — Ollama LLM Client
=======================================
Async HTTP client for Ollama's /api/generate endpoint.
Uses streaming=false so the full response arrives in one JSON payload.

Handles:
  - Connection refused (Ollama not started)
  - HTTP errors
  - Timeout (LLM slow on CPU)
  - Non-JSON or partial response
  - JSON embedded inside Markdown code fences (common with some models)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import cfg

log = logging.getLogger("rca.llm")

# Regex to extract JSON from ```json ... ``` or ``` ... ``` code fences
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class LLMClient:
    """Thin async wrapper for Ollama /api/generate."""

    def __init__(self) -> None:
        self._base = cfg.llm_url.rstrip("/")

    async def generate(self, prompt: str) -> dict[str, Any]:
        """
        Call Ollama and return the parsed JSON response dict.

        Returns a dict with at least {"response": "<raw text>"}.
        Raises RuntimeError on unrecoverable failure (caller decides fallback).
        """
        payload = {
            "model":   cfg.llm_model,
            "prompt":  prompt,
            "stream":  False,
            "options": {
                "temperature":  cfg.llm_temperature,
                "num_predict":  cfg.llm_max_tokens,
            },
        }

        log.debug(
            "Calling Ollama model=%s url=%s/api/generate prompt_len=%d",
            cfg.llm_model, self._base, len(prompt),
        )

        try:
            async with httpx.AsyncClient(timeout=cfg.llm_timeout) as client:
                resp = await client.post(
                    f"{self._base}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                log.debug(
                    "Ollama responded: done=%s eval_count=%s",
                    data.get("done"), data.get("eval_count"),
                )
                return data

        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self._base}. "
                "Is it running? Start with: ollama serve"
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama request timed out after {cfg.llm_timeout}s. "
                "Try a smaller model or increase OLLAMA_TIMEOUT."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Ollama unexpected error: {exc}") from exc

    async def is_healthy(self) -> bool:
        """Return True if Ollama is reachable and the target model exists."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base}/api/tags")
                if r.status_code != 200:
                    return False
                models = [m.get("name", "") for m in r.json().get("models", [])]
                # Accept prefix match (e.g. "llama3.2" matches "llama3.2:latest")
                model_name = cfg.llm_model
                return any(m.startswith(model_name) for m in models)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return model names available in Ollama."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base}/api/tags")
                r.raise_for_status()
                return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return []


def extract_json_from_response(raw_text: str) -> dict[str, Any]:
    """
    Parse the LLM's text output into a Python dict.

    Strategy (in order):
      1. Try direct JSON parse of the full text.
      2. Strip Markdown code fences and retry.
      3. Find the first '{' ... last '}' substring and try parsing that.

    Raises ValueError if no valid JSON can be extracted.
    """
    text = raw_text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip code fences
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: substring between first { and last }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Cannot extract JSON from LLM response. "
        f"First 200 chars: {raw_text[:200]!r}"
    )
