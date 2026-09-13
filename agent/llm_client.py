"""HTTP clients for OpenAI-compatible LLM APIs and LM Studio control."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import tiktoken

from agent.schemas import ModelLoadResult, ModelLoadStatus
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

LOAD_TIMEOUT = 120.0
UNLOAD_TIMEOUT = 10.0
EMERGENCY_UNLOAD_TIMEOUT = 5.0


class LLMClient:
    """OpenAI-compatible streaming chat client."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout or settings.LLM_TIMEOUT
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Count tokens using the cl100k_base encoding."""
        return len(self._encoding.encode(text))

    async def complete_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        """Return a full non-streaming chat completion."""
        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion tokens via SSE."""
        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    try:
                        async for token in self._parse_sse_stream(response):
                            yield token
                    except asyncio.CancelledError:
                        await response.aclose()
                        raise
        except httpx.TimeoutException as exc:
            logger.warning("llm_stream_timeout", error=str(exc))
            raise

    async def _parse_sse_stream(
        self,
        response: httpx.Response,
    ) -> AsyncGenerator[str, None]:
        """Parse Server-Sent Events from a streaming chat response."""
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content


class LMStudioClient:
    """LM Studio control and OpenAI-compatible model listing."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.LM_STUDIO_BASE_URL).rstrip("/")
        self._openai_base = f"{self._base_url}/v1"
        self._control_base = self._base_url
        self._model_switch_lock = asyncio.Lock()
        self._current_loaded_model: str | None = None

    async def list_models(self) -> list[dict[str, Any]]:
        """Return available models from the OpenAI-compatible endpoint."""
        url = f"{self._openai_base}/models"
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            return payload.get("data", [])

    async def is_model_loaded(self, model_id: str) -> bool:
        """Check whether the given model is currently loaded."""
        models = await self.list_models()
        for model in models:
            if model.get("id") == model_id:
                return bool(model.get("loaded", False))
        return False

    async def load_model(
        self,
        model_id: str,
        gpu_offload: int,
        context_length: int | None,
    ) -> ModelLoadResult:
        """Load a model via the LM Studio control API."""
        async with self._model_switch_lock:
            return await self._load_model_locked(
                model_id,
                gpu_offload,
                context_length,
            )

    async def _load_model_locked(
        self,
        model_id: str,
        gpu_offload: int,
        context_length: int | None,
    ) -> ModelLoadResult:
        """Perform model load while holding the switch lock."""
        if (
            self._current_loaded_model is not None
            and self._current_loaded_model != model_id
        ):
            await self._unload_model_http(self._current_loaded_model)

        url = f"{self._control_base}/api/v0/models/load"
        body: dict[str, Any] = {
            "model": model_id,
            "gpu_offload": gpu_offload,
        }
        if context_length is not None:
            body["context_length"] = context_length

        try:
            async with httpx.AsyncClient(timeout=LOAD_TIMEOUT) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("lm_studio_load_timeout", model_id=model_id)
            await self._emergency_unload(model_id)
            return ModelLoadResult(
                status=ModelLoadStatus.ERROR,
                message=f"Model load timed out for {model_id}",
                model_id=model_id,
            )
        except httpx.ConnectError:
            logger.warning("lm_studio_unreachable")
            return ModelLoadResult(
                status=ModelLoadStatus.UNREACHABLE,
                message="LM Studio is not running",
                model_id=model_id,
            )
        except httpx.HTTPError as exc:
            logger.error("lm_studio_load_error", model_id=model_id, error=str(exc))
            return ModelLoadResult(
                status=ModelLoadStatus.ERROR,
                message=str(exc),
                model_id=model_id,
            )

        self._current_loaded_model = model_id
        return ModelLoadResult(
            status=ModelLoadStatus.LOADED,
            message=f"Model {model_id} loaded successfully",
            model_id=model_id,
        )

    async def unload_model(self, model_id: str) -> ModelLoadResult:
        """Unload a model via the LM Studio control API."""
        async with self._model_switch_lock:
            return await self._unload_model_locked(model_id)

    async def _unload_model_locked(self, model_id: str) -> ModelLoadResult:
        """Perform model unload while holding the switch lock."""
        try:
            await self._unload_model_http(model_id)
        except httpx.ConnectError:
            logger.warning("lm_studio_unreachable")
            return ModelLoadResult(
                status=ModelLoadStatus.UNREACHABLE,
                message="LM Studio is not running",
                model_id=model_id,
            )
        except httpx.HTTPError as exc:
            logger.error("lm_studio_unload_error", model_id=model_id, error=str(exc))
            return ModelLoadResult(
                status=ModelLoadStatus.ERROR,
                message=str(exc),
                model_id=model_id,
            )

        if self._current_loaded_model == model_id:
            self._current_loaded_model = None
        return ModelLoadResult(
            status=ModelLoadStatus.IDLE,
            message=f"Model {model_id} unloaded",
            model_id=model_id,
        )

    async def _unload_model_http(self, model_id: str) -> None:
        """Send an unload request to LM Studio."""
        url = f"{self._control_base}/api/v0/models/unload"
        async with httpx.AsyncClient(timeout=UNLOAD_TIMEOUT) as client:
            response = await client.post(url, json={"model": model_id})
            response.raise_for_status()

    async def _emergency_unload(self, model_id: str) -> None:
        """Force-unload a model with a hard 5-second timeout."""
        try:
            await asyncio.wait_for(
                self._unload_model_http(model_id),
                timeout=EMERGENCY_UNLOAD_TIMEOUT,
            )
        except (asyncio.TimeoutError, httpx.HTTPError) as exc:
            logger.error(
                "lm_studio_emergency_unload_failed",
                model_id=model_id,
                error=str(exc),
            )
        if self._current_loaded_model == model_id:
            self._current_loaded_model = None


llm_client = LLMClient(
    base_url=settings.LM_STUDIO_BASE_URL,
    api_key=settings.DEEPSEEK_API_KEY,
)
