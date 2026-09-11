"""HTTP client for the Hojo TTS app."""

from __future__ import annotations

from typing import Any

import aiohttp

from .models import ModelInfo, VoiceInfo

_API_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Synthesis is CPU-bound and grows with the length of the text. A long weather
# summary on a slow host can legitimately take tens of seconds, so this is
# generous — the pipeline's own timeout is the real ceiling.
_SPEAK_TIMEOUT = aiohttp.ClientTimeout(total=180)


class HojoTTSError(aiohttp.ClientError):
    """The server rejected a request, carrying its error code."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialize with the server-provided error code."""
        super().__init__(message)
        self.code = code


class HojoTTSClient:
    """Async client for the Hojo TTS HTTP API."""

    def __init__(self, host: str, api_key: str, session: aiohttp.ClientSession) -> None:
        """Initialize the client.

        Args:
            host: Base URL of the app, e.g. ``http://homeassistant.local:8771``.
            api_key: Bearer token for the ``/api`` routes.
            session: Home Assistant's shared aiohttp session.
        """
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._session = session

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def health(self) -> dict[str, Any]:
        """Return the unauthenticated health payload."""
        async with self._session.get(
            f"{self._host}/health", timeout=_API_TIMEOUT
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def validate(self) -> str | None:
        """Check connectivity and auth.

        Returns:
            ``None`` when usable, otherwise ``cannot_connect`` or
            ``invalid_api_key`` — the translation keys the config flow shows.
        """
        try:
            await self.health()
        except aiohttp.ClientError, TimeoutError:
            return "cannot_connect"

        try:
            async with self._session.get(
                f"{self._host}/api/models",
                headers=self._headers,
                timeout=_API_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    return "invalid_api_key"
                response.raise_for_status()
        except aiohttp.ClientError, TimeoutError:
            return "cannot_connect"

        return None

    async def list_models(self) -> list[ModelInfo]:
        """List every model the server knows, downloaded or not."""
        async with self._session.get(
            f"{self._host}/api/models", headers=self._headers, timeout=_API_TIMEOUT
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        return [
            ModelInfo(
                id=item["id"],
                name=item["name"],
                description=item.get("description", ""),
                kind=item.get("kind", "preset"),
                languages=item.get("languages") or [],
                sample_rate=item.get("sample_rate", 24000),
                downloaded=bool(item.get("downloaded")),
                loaded=bool(item.get("loaded")),
                rtf_hint=float(item.get("rtf_hint") or 0.0),
            )
            for item in payload
        ]

    async def list_voices(self, model_id: str | None = None) -> list[VoiceInfo]:
        """List voices, optionally for a single model."""
        params = {"model": model_id} if model_id else None
        async with self._session.get(
            f"{self._host}/api/voices",
            headers=self._headers,
            params=params,
            timeout=_API_TIMEOUT,
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        return [
            VoiceInfo(
                id=item["id"],
                name=item.get("name", item["id"]),
                language=item.get("language"),
                gender=item.get("gender", "unknown"),
                source=item.get("source", "builtin"),
            )
            for item in payload
        ]

    async def speak(
        self,
        text: str,
        *,
        model: str,
        voice: str | None,
        audio_format: str = "wav",
        normalize_text: bool = True,
        convert_script: bool = True,
    ) -> tuple[bytes, dict[str, float]]:
        """Synthesise text.

        Args:
            text: What to say, in whatever script the user writes.
            model: Model id.
            voice: Voice id, or ``None`` to let the server pick its default.
            audio_format: Container to request.
            normalize_text: Expand numbers, units and clock literals.
            convert_script: Convert Traditional Chinese glyphs to Simplified.

        Returns:
            The encoded audio and the server's timing headers.

        Raises:
            HojoTTSError: The server rejected the request.
        """
        body: dict[str, Any] = {
            "text": text,
            "model": model,
            "format": audio_format,
            "normalize_text": normalize_text,
            "convert_script": convert_script,
        }
        if voice:
            body["voice"] = voice

        async with self._session.post(
            f"{self._host}/api/speak",
            headers=self._headers,
            json=body,
            timeout=_SPEAK_TIMEOUT,
        ) as response:
            if response.status >= 400:
                code, message = await _error_detail(response)
                raise HojoTTSError(message, code=code)
            audio = await response.read()
            stats = {
                "inference_ms": _header_float(response, "X-Hojo-Inference-Ms"),
                "audio_seconds": _header_float(response, "X-Hojo-Audio-Seconds"),
                "rtf": _header_float(response, "X-Hojo-Rtf"),
            }
        return audio, stats


async def _error_detail(response: aiohttp.ClientResponse) -> tuple[str, str]:
    """Pull the server's error code and message out of a failed response."""
    try:
        payload = await response.json()
    except aiohttp.ClientError, ValueError:
        return "HTTP_ERROR", f"{response.status} {response.reason}"
    if isinstance(payload, dict):
        return (
            str(payload.get("code", "HTTP_ERROR")),
            str(payload.get("message", response.reason)),
        )
    return "HTTP_ERROR", f"{response.status} {response.reason}"


def _header_float(response: aiohttp.ClientResponse, name: str) -> float:
    try:
        return float(response.headers.get(name, "0"))
    except ValueError:
        return 0.0
