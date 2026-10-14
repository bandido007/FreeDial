"""OpenAI's speech-to-text, chat (tool calls) and text-to-speech, over HTTP (aiohttp).

Speech comes back as raw 24 kHz 16-bit PCM, streamed, which is exactly what the call's media
leg (slin24) takes: no resampling anywhere in the worker.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from aiworker.core.audio import RATE, wav

API = "https://api.openai.com/v1"
LANGUAGE_CODES = {"swahili": "sw", "english": "en"}


class OpenAiError(RuntimeError):
    pass


def reasoning_effort(model: str) -> str | None:
    """The lowest reasoning a voice turn can bear: none where the model allows it, else minimal."""
    if model.startswith(("gpt-5.1", "gpt-5.2", "gpt-5.4")) and "chat" not in model and "codex" not in model:
        return "none"
    if model in ("gpt-5", "gpt-5-mini", "gpt-5-nano") or model.startswith(
        ("gpt-5-2025", "gpt-5-mini-", "gpt-5-nano-")
    ):
        return "minimal"
    return None


class OpenAi:
    def __init__(self, http: aiohttp.ClientSession, key: str, base: str = API):
        self.http, self.base = http, base
        self.headers = {"Authorization": f"Bearer {key}"}

    async def _check(self, response: aiohttp.ClientResponse) -> None:
        if response.status >= 300:
            raise OpenAiError(
                f"OpenAI {response.url.path} -> {response.status}: {(await response.text())[:300]}"
            )

    async def transcribe(
        self, pcm: bytes, *, model: str, language: str | None = None, rate: int = RATE, prompt: str = ""
    ) -> str:
        form = aiohttp.FormData()
        form.add_field("file", wav(pcm, rate), filename="turn.wav", content_type="audio/wav")
        form.add_field("model", model)
        form.add_field("response_format", "json")
        if language in LANGUAGE_CODES:
            form.add_field("language", LANGUAGE_CODES[language or ""])
        if prompt:
            form.add_field("prompt", prompt)
        timeout = aiohttp.ClientTimeout(total=20)
        async with self.http.post(
            f"{self.base}/audio/transcriptions", data=form, headers=self.headers, timeout=timeout
        ) as response:
            await self._check(response)
            return str((await response.json()).get("text") or "").strip()

    async def decide(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "required",
    ) -> tuple[str, dict[str, Any]]:
        """The one tool the model calls, and its arguments."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
        }
        effort = reasoning_effort(model)
        if effort:
            body["reasoning_effort"] = effort
        timeout = aiohttp.ClientTimeout(total=20)
        async with self.http.post(
            f"{self.base}/chat/completions", json=body, headers=self.headers, timeout=timeout
        ) as response:
            await self._check(response)
            data = await response.json()
        message = data["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:  # a plain answer despite "required": speak it
            return "reply", {"text": message.get("content") or ""}
        function = calls[0]["function"]
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            raise OpenAiError(f"tool arguments are not JSON: {exc}") from exc
        return str(function["name"]), args if isinstance(args, dict) else {}

    async def speak(
        self, text: str, *, model: str, voice: str, style: str = "", timeout: float = 30
    ) -> AsyncIterator[bytes]:
        """``text`` as speech: 24 kHz 16-bit mono PCM, in chunks as it is made."""
        body: dict[str, Any] = {"model": model, "voice": voice, "input": text, "response_format": "pcm"}
        if style and model.startswith("gpt-"):  # tts-1 and tts-1-hd take no instructions
            body["instructions"] = style
        limit = aiohttp.ClientTimeout(total=timeout)
        async with self.http.post(
            f"{self.base}/audio/speech", json=body, headers=self.headers, timeout=limit
        ) as response:
            await self._check(response)
            leftover = b""
            async for chunk in response.content.iter_chunked(4800):
                data = leftover + chunk
                usable = len(data) - len(data) % 2
                leftover = data[usable:]
                if usable:
                    yield data[:usable]
