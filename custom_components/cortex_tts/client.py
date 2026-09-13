"""HTTP client for the Cortex TTS app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .const import STREAM_FORMAT
from .models import ModelInfo, VoiceInfo

_API_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Synthesis is CPU-bound and grows with the length of the text, so the limit
# is on silence from the server rather than on the whole exchange: a stream
# that keeps sending frames may run as long as the reply is, and one that
# stops sending is what a stuck server looks like.
_SPEAK_TIMEOUT = aiohttp.ClientTimeout(sock_connect=10, sock_read=180)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(sock_connect=10, sock_read=60)

# The wire shape this client was written against. The server reports its own
# in /health; a mismatch is refused at setup rather than discovered as a
# header that reads zero or a field that raises.
SUPPORTED_API_VERSION = 1


class CortexTTSError(Exception):
    """The server answered, and the answer was a refusal.

    Not an `aiohttp.ClientError`: that family means the request never got an
    answer, and the two are handled differently — a refusal is reported as
    what the server said, a transport failure as "cannot connect".
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialize with the server-provided error code."""
        super().__init__(message)
        self.code = code


class CortexTTSAuthError(CortexTTSError):
    """The stored key was rejected; the entry needs re-authentication."""


class CortexTTSClient:
    """Async client for the Cortex TTS HTTP API."""

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
            await _raise_for_status(response)
            return await response.json()

    async def validate(self) -> str | None:
        """Check connectivity, auth and the wire version.

        Returns:
            ``None`` when usable, otherwise ``cannot_connect``,
            ``invalid_api_key`` or ``unsupported_api`` — the translation keys
            the config flow shows.
        """
        try:
            health = await self.health()
        except aiohttp.ClientError, TimeoutError, CortexTTSError:
            return "cannot_connect"
        # A server too old to report one speaks version 1; the field was
        # added without changing anything it describes.
        if int(health.get("api_version", SUPPORTED_API_VERSION)) != (
            SUPPORTED_API_VERSION
        ):
            return "unsupported_api"

        try:
            async with self._session.get(
                f"{self._host}/api/models",
                headers=self._headers,
                timeout=_API_TIMEOUT,
            ) as response:
                await _raise_for_status(response)
        except CortexTTSAuthError:
            return "invalid_api_key"
        except aiohttp.ClientError, TimeoutError, CortexTTSError:
            return "cannot_connect"

        return None

    async def list_models(self) -> list[ModelInfo]:
        """List every model the server knows, downloaded or not."""
        async with self._session.get(
            f"{self._host}/api/models", headers=self._headers, timeout=_API_TIMEOUT
        ) as response:
            await _raise_for_status(response)
            payload = await response.json()

        # Only `id`, `name`, `languages` and `downloaded` decide anything
        # here; the capability flags are carried for diagnostics and must
        # not be able to take the entry down when the server drops one.
        return [
            ModelInfo(
                id=item["id"],
                name=item["name"],
                description=item.get("description", ""),
                builtin_voices=bool(item.get("builtin_voices", False)),
                cloning=bool(item.get("cloning", False)),
                chunk_streaming=bool(item.get("chunk_streaming", False)),
                languages=item.get("languages") or [],
                sample_rate=item.get("sample_rate", 24000),
                downloaded=bool(item.get("downloaded")),
                loaded=bool(item.get("loaded")),
                language_choice=bool(item.get("language_choice", False)),
                style_instruction=bool(item.get("style_instruction", False)),
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
            await _raise_for_status(response)
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
        spoken_language: str | None = None,
        instruct: str | None = None,
    ) -> tuple[bytes, dict[str, float]]:
        """Synthesise text.

        Args:
            text: What to say, in whatever script the user writes.
            model: Model id.
            voice: Voice id, or ``None`` to let the server pick its default.
            audio_format: Container to request.
            normalize_text: Expand numbers, units and clock literals.
            convert_script: Convert Traditional Chinese glyphs to Simplified.
            spoken_language: Which language the model reads the text as, on
                the models that take one. ``None`` lets the voice decide.
            instruct: A plain-language instruction beside the voice, on the
                one model that takes one.

        Returns:
            The encoded audio and the server's timing headers.

        Raises:
            CortexTTSError: The server rejected the request.
        """
        body = _speak_body(
            text,
            model=model,
            voice=voice,
            normalize_text=normalize_text,
            convert_script=convert_script,
            spoken_language=spoken_language,
            instruct=instruct,
        )
        body["format"] = audio_format

        async with self._session.post(
            f"{self._host}/api/speak",
            headers=self._headers,
            json=body,
            timeout=_SPEAK_TIMEOUT,
        ) as response:
            await _raise_for_status(response)
            audio = await response.read()
            stats = {
                "inference_ms": _header_float(response, "X-Cortex-Inference-Ms"),
                "audio_seconds": _header_float(response, "X-Cortex-Audio-Seconds"),
                "rtf": _header_float(response, "X-Cortex-Rtf"),
            }
        return audio, stats

    async def speak_stream(
        self,
        text: str,
        *,
        model: str,
        voice: str | None,
        normalize_text: bool = True,
        convert_script: bool = True,
        spoken_language: str | None = None,
        instruct: str | None = None,
    ) -> AsyncIterator[tuple[int, bytes]]:
        """Synthesise text, yielding audio as the server produces it.

        MP3, which is a bare sequence of self-describing frames: playable from
        the first chunk, and with no length to declare. A WAV stream has to
        declare one before the audio exists, and a general-purpose player waits
        for whatever length it declares.

        Yields:
            ``(bitrate, chunk)`` in order, where bitrate is bits per second of
            the encoded stream. A byte count over it is how long that audio
            plays, which is what sizes a buffer without decoding anything.

        Raises:
            CortexTTSError: The server rejected the request. Errors arrive
                before any audio does — once the response has started the
                status is 200, so a later failure surfaces as a short stream.
        """
        body = _speak_body(
            text,
            model=model,
            voice=voice,
            normalize_text=normalize_text,
            convert_script=convert_script,
            spoken_language=spoken_language,
            instruct=instruct,
        )
        body["format"] = STREAM_FORMAT

        async with self._session.post(
            f"{self._host}/api/speak/stream",
            headers=self._headers,
            json=body,
            timeout=_STREAM_TIMEOUT,
        ) as response:
            await _raise_for_status(response)
            bitrate = _header_int(response, "X-Cortex-Bitrate")
            if bitrate <= 0:
                raise CortexTTSError(
                    "the server sent no bitrate, so there is no way to tell how "
                    "much audio a chunk holds",
                    code="malformed_stream",
                )
            async for chunk in response.content.iter_any():
                if chunk:
                    yield bitrate, chunk


async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
    """Turn a refusal into the server's own code and message.

    Raises:
        CortexTTSAuthError: 401 or 403.
        CortexTTSError: Any other status of 400 or above.
    """
    if response.status < 400:
        return
    code, message = await _error_detail(response)
    if response.status in (401, 403):
        raise CortexTTSAuthError(message, code=code)
    raise CortexTTSError(message, code=code)


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


def _speak_body(
    text: str,
    *,
    model: str,
    voice: str | None,
    normalize_text: bool,
    convert_script: bool,
    spoken_language: str | None,
    instruct: str | None,
) -> dict[str, Any]:
    """Build the request both speak paths send.

    The two optional fields are omitted rather than sent empty: the server
    refuses one a model does not declare, and "" would be a request for
    something.
    """
    body: dict[str, Any] = {
        "text": text,
        "model": model,
        "normalize_text": normalize_text,
        "convert_script": convert_script,
    }
    if voice:
        body["voice"] = voice
    if spoken_language:
        body["language"] = spoken_language
    if instruct:
        body["instruct"] = instruct
    return body


def _header_float(response: aiohttp.ClientResponse, name: str) -> float:
    try:
        return float(response.headers.get(name, "0"))
    except ValueError:
        return 0.0


def _header_int(response: aiohttp.ClientResponse, name: str) -> int:
    """Return a whole-number header, or 0 when it is absent or malformed."""
    try:
        return int(response.headers.get(name, "0"))
    except ValueError:
        return 0
