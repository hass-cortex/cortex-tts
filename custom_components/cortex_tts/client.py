"""HTTP client for the Cortex TTS app."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .const import STREAM_FORMAT
from .models import ModelInfo, VoiceInfo

_API_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Synthesis is CPU-bound and grows with the length of the text, so the limit
# is on silence from the server rather than on the whole exchange: a long
# reply may take as long as it takes, and a server that has stopped answering
# is what a stuck one looks like.
_SPEAK_TIMEOUT = aiohttp.ClientTimeout(sock_connect=10, sock_read=180)

# A live reply waits on two writers — the conversation agent for text and the
# model for audio — so silence on the socket is bounded rather than the reply:
# a model that has not produced a frame in this long is stuck, not slow.
# attrs fields declared with `attr.ib(type=...)`, which the checker cannot
# read as parameters; the names are aiohttp's own.
_LIVE_TIMEOUT = aiohttp.ClientWSTimeout(
    ws_receive=120,  # pyright: ignore[reportCallIssue]
    ws_close=5,  # pyright: ignore[reportCallIssue]
)

_LOGGER = logging.getLogger(__name__)

# The wire shape this client was written against. The server reports its own
# in /health; a mismatch is refused at setup rather than discovered as a
# header that reads zero or a field that raises.
SUPPORTED_API_VERSION = 4


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


class LiveSession:
    """One reply over `/api/speak/live`: words in, audio out.

    Text goes in as the writer produces it; frames come back as the server
    renders them. The server decides when to render what, so nothing here
    splits, groups or holds — it forwards, and it makes sure the server hears
    when the listener has gone.
    """

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Wrap an open socket on which the start frame has been sent."""
        self._ws = ws
        self._finished = False

    async def send_text(self, text: str) -> None:
        """Forward a piece of the reply, as the writer wrote it."""
        if text:
            await self._ws.send_json({"type": "text", "text": text})

    async def end(self) -> None:
        """Tell the server the reply is complete."""
        await self._ws.send_json({"type": "end"})

    async def cancel(self) -> None:
        """Tell the server nobody is listening, so it stops rendering.

        Best effort: a socket that is already closed has delivered the same
        news, and there is no one left to report a failure to.
        """
        if self._finished or self._ws.closed:
            return
        with contextlib.suppress(aiohttp.ClientError, ConnectionError, RuntimeError):
            await self._ws.send_json({"type": "cancel"})

    async def frames(self) -> AsyncIterator[tuple[str, Any]]:
        """Yield what the server sends, in order.

        Yields:
            ``("ready", dict)`` once the model and voice are settled,
            ``("audio", bytes)`` per audio frame, and ``("done", dict)`` last.

        Raises:
            CortexTTSError: The server refused the reply or failed it. Its
                own code and message, as on the HTTP routes.
            CortexTTSAuthError: The handshake was accepted and then closed as
                a policy violation, which is how a WebSocket says 401.
        """
        async for message in self._ws:
            if message.type is aiohttp.WSMsgType.BINARY:
                yield "audio", message.data
                continue
            if message.type is not aiohttp.WSMsgType.TEXT:
                break
            frame = json.loads(message.data)
            kind = frame.get("type")
            if kind == "error":
                self._finished = True
                raise CortexTTSError(
                    str(frame.get("message", "the server refused the reply")),
                    code=str(frame.get("code", "ERROR")),
                )
            if kind in ("ready", "batch", "done"):
                yield kind, frame
            if kind == "done":
                self._finished = True
                return
        # Closed without a `done`: a refused key closes with policy violation
        # before saying anything else.
        if self._ws.close_code == 1008:
            raise CortexTTSAuthError("authentication required", code="AUTH_REQUIRED")
        self._finished = True


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
        # A server too old to report one speaks version 1, which has no live
        # endpoint; it is refused the same way as any other version.
        if int(health.get("api_version", 1)) != SUPPORTED_API_VERSION:
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

    @contextlib.asynccontextmanager
    async def speak_live(
        self,
        *,
        model: str,
        voice: str | None,
        mode: str,
        normalize_text: bool | None = None,
        expand_numbers: bool | None = None,
        convert_script: bool | None = None,
        taiwan_readings: bool | None = None,
        spoken_language: str | None = None,
        instruct: str | None = None,
    ) -> AsyncIterator[LiveSession]:
        """Open a live reply: text forwarded as it is written, audio as rendered.

        Leaving the block early — the consumer stopped reading — sends
        `cancel` and closes the socket, so the server stops rendering for a
        listener that has gone. Leaving after `done` just closes.

        Args:
            mode: ``auto`` lets the server pace the reply; ``buffered`` holds
                everything until it is rendered.

        Raises:
            CortexTTSError: Raised from `LiveSession.frames`, not here: the
                server answers on the socket, after the handshake.
        """
        ws = await self._session.ws_connect(
            f"{self._host}/api/speak/live",
            headers=self._headers,
            timeout=_LIVE_TIMEOUT,
        )
        session = LiveSession(ws)
        start = _speak_common(
            model=model,
            voice=voice,
            normalize_text=normalize_text,
            expand_numbers=expand_numbers,
            convert_script=convert_script,
            taiwan_readings=taiwan_readings,
            spoken_language=spoken_language,
            instruct=instruct,
        )
        start.update(type="start", format=STREAM_FORMAT, mode=mode)
        try:
            await ws.send_json(start)
            yield session
        finally:
            await session.cancel()
            with contextlib.suppress(aiohttp.ClientError, ConnectionError):
                await ws.close()


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


def _speak_common(
    *,
    model: str,
    voice: str | None,
    normalize_text: bool | None,
    expand_numbers: bool | None,
    convert_script: bool | None,
    taiwan_readings: bool | None,
    spoken_language: str | None,
    instruct: str | None,
) -> dict[str, Any]:
    """What both ways of asking for speech settle — the server's `SpeakCommon`.

    The words and the container are the caller's to add: a file request sends
    its text in the body, a live reply sends it in frames afterwards.

    The optional fields are omitted rather than sent empty: an absent switch
    is one the server decides from the language, the server refuses an
    instruction a model does not declare, and "" would be a request for
    something.
    """
    body: dict[str, Any] = {"model": model}
    if normalize_text is not None:
        body["normalize_text"] = normalize_text
    if expand_numbers is not None:
        body["expand_numbers"] = expand_numbers
    if convert_script is not None:
        body["convert_script"] = convert_script
    if taiwan_readings is not None:
        body["taiwan_readings"] = taiwan_readings
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
