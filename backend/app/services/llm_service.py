"""Structured LLM Abstraction with Context Safety & Multi-Tier Fallback Ladder.

Implements Phase 5.4 of Master Specification:
- Standardized generate_structured interface across all graph nodes
- Pre-flight context token length checks and safe truncation (prevents silent overflow crashes)
- Robust fallback ladder: Ollama (RTX 3050) -> Groq Cloud -> Mock / Heuristics
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
import httpx
from pydantic import BaseModel

from app.config import settings
from app.services.mock_providers import MockLLMProvider

logger = logging.getLogger("llm_service")

# Average 4 characters per token heuristic
CHARS_PER_TOKEN = 4
DEFAULT_MAX_CONTEXT_TOKENS = 4096


def truncate_context_safely(text: str, max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> str:
    """Pre-flight check: Truncate context cleanly if it exceeds maximum model token window."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text

    logger.warning("Context length (%d chars) exceeds token budget (%d tokens). Truncating safely.", len(text), max_tokens)
    # Keep the initial instructions and the most recent context
    head_size = int(max_chars * 0.3)
    tail_size = int(max_chars * 0.7) - 100
    return text[:head_size] + "\n... [Context truncated to fit model token limit] ...\n" + text[-tail_size:]


class LLMService:
    """Enterprise multi-tier LLM engine with fallback ladder."""

    @staticmethod
    async def generate_structured(
        prompt: str,
        system_prompt: str = "You are Hermes, an autonomous AI software agency engine. Respond with valid JSON.",
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> dict[str, Any]:
        """Generate structured JSON output with context pre-flight and multi-tier fallback."""
        # 1. Check if running in TEST_MODE
        if settings.TEST_MODE:
            mock = MockLLMProvider()
            return await mock.generate_structured(prompt)

        # 2. Pre-flight context length check
        safe_prompt = truncate_context_safely(prompt, max_context_tokens)

        # Tier 1: Local Ollama (Qwen 3 4B on RTX 3050)
        try:
            ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
            payload = {
                "model": settings.OLLAMA_MODEL,
                "prompt": f"{system_prompt}\n\n{safe_prompt}",
                "stream": False,
                "format": "json",
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(ollama_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_text = data.get("response", "{}")
                    try:
                        return json.loads(raw_text)
                    except json.JSONDecodeError:
                        logger.warning("Ollama output was not clean JSON, trying regex extraction")
                        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                        if match:
                            return json.loads(match.group(0))
        except Exception as e:
            logger.info("Tier 1 (Ollama) failed or unavailable (%s), trying Tier 1.5 (llama.cpp)...", e)

        # Tier 1.5: Local llama.cpp (High-Performance GGUF C++ Server)
        llama_cpp_url = getattr(settings, "LLAMA_CPP_BASE_URL", "http://localhost:8001/v1")
        if getattr(settings, "LLAMA_CPP_ENABLED", False):
            try:
                result = await LLMService.query_llama_cpp(safe_prompt, system_prompt)
                if result:
                    return result
            except Exception as e:
                logger.info("Tier 1.5 (llama.cpp) failed or unavailable (%s), falling back to Tier 2 (Groq Cloud)...", e)

        # Tier 2: Groq Cloud (Qwen 3.8 27B)
        groq_api_key = getattr(settings, "GROQ_API_KEY", "")
        if groq_api_key and not groq_api_key.startswith("your_"):
            try:
                headers = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                }
                groq_payload = {
                    "model": "qwen/qwen3.8-27b",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": safe_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=groq_payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        return json.loads(content)
            except Exception as e:
                logger.warning("Tier 2 (Groq Cloud) failed (%s), falling back to Tier 3 (Heuristics)...", e)

        # Tier 3: Deterministic fallback
        mock = MockLLMProvider()
        return await mock.generate_structured(safe_prompt)

    @staticmethod
    async def query_llama_cpp(
        prompt: str,
        system_prompt: str = "You are Hermes, an autonomous AI software agency engine. Respond with valid JSON.",
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 25.0,
    ) -> dict[str, Any] | None:
        """Execute inference against local llama.cpp llama-server."""
        url = (base_url or settings.LLAMA_CPP_BASE_URL).rstrip("/") + "/chat/completions"
        target_model = model or settings.LLAMA_CPP_MODEL
        payload = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", content, re.DOTALL)
                    if m:
                        return json.loads(m.group(0))
            return None
