"""The HTTP client, against a real aiohttp server.

A stubbed transport would test the stub. These spin up an actual server, so
the status handling, the headers and the bearer token are exercised as they
are in the app.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.cortex_tts.client import (
    CortexTTSAuthError,
    CortexTTSClient,
    CortexTTSError,
)

Handler = Callable[[web.Request], object]


@asynccontextmanager
async def _client(**routes: Handler) -> AsyncIterator[CortexTTSClient]:
    """Serve `routes` (keyed `METHOD_path_with_underscores`) to one client."""
    app = web.Application()
    for name, handler in routes.items():
        method, _, path = name.partition("_")
        app.router.add_route(method.upper(), "/" + path.replace("_", "/"), handler)  # type: ignore[arg-type]
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession()
    try:
        yield CortexTTSClient(str(server.make_url("")).rstrip("/"), "test-key", session)
    finally:
        await session.close()
        await server.close()


async def _ok(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


class TestValidate:
    """What the config flow shows, for each way this can fail."""

    async def test_a_reachable_authorised_server_is_usable(self) -> None:
        async def models(_: web.Request) -> web.Response:
            return web.json_response([])

        async with _client(get_health=_ok, get_api_models=models) as client:
            assert await client.validate() is None

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_key_is_named_as_such(self, status: int) -> None:
        async def refuse(_: web.Request) -> web.Response:
            return web.Response(status=status)

        async with _client(get_health=_ok, get_api_models=refuse) as client:
            assert await client.validate() == "invalid_api_key"

    async def test_an_unreachable_server_is_not_an_auth_problem(self) -> None:
        # The distinction is the point: one is the user's key, the other is
        # their network, and the config flow says different things.
        async with _client(get_health=_ok) as client:
            host = client._host

        # The server is stopped now, so the port refuses at once — which is
        # what a stopped app looks like, without waiting out a timeout.
        async with aiohttp.ClientSession() as session:
            gone = CortexTTSClient(host, "test-key", session)
            assert await gone.validate() == "cannot_connect"


class TestAuth:
    async def test_the_bearer_token_is_sent_on_api_routes(self) -> None:
        seen: dict[str, str] = {}

        async def record(request: web.Request) -> web.Response:
            seen["auth"] = request.headers.get("Authorization", "")
            return web.json_response([])

        async with _client(get_health=_ok, get_api_models=record) as client:
            await client.validate()
        assert seen["auth"] == "Bearer test-key"

    async def test_a_trailing_slash_on_the_host_is_dropped(self) -> None:
        async with _client(get_health=_ok) as client:
            host = client._host
            assert not host.endswith("/")


class TestSpeak:
    @staticmethod
    def _speaker(
        *,
        status: int = 200,
        body: bytes = b"RIFF....",
        headers: dict[str, str] | None = None,
    ) -> Handler:
        async def handler(_: web.Request) -> web.Response:
            if status >= 400:
                return web.json_response(
                    {"code": "MODEL_NOT_READY", "message": "not downloaded"},
                    status=status,
                )
            return web.Response(body=body, headers=headers or {})

        return handler

    async def test_the_timing_headers_come_back_as_numbers(self) -> None:
        async with _client(
            post_api_speak=self._speaker(
                headers={
                    "X-Cortex-Inference-Ms": "2283.0",
                    "X-Cortex-Audio-Seconds": "4.22",
                    "X-Cortex-Rtf": "0.541",
                }
            )
        ) as client:
            audio, stats = await client.speak(
                "室內溫度是二十六度。", model="hojo-40m", voice="hojo_zh_f_01"
            )
        assert audio == b"RIFF...."
        assert stats == {"inference_ms": 2283.0, "audio_seconds": 4.22, "rtf": 0.541}

    async def test_a_missing_header_reads_as_zero_not_a_crash(self) -> None:
        async with _client(post_api_speak=self._speaker()) as client:
            _, stats = await client.speak("x", model="hojo-40m", voice=None)
        assert stats["rtf"] == 0.0

    async def test_the_servers_own_message_reaches_the_caller(self) -> None:
        # Home Assistant surfaces this string, so it must be the app's own and
        # not "500 Internal Server Error".
        async with _client(post_api_speak=self._speaker(status=409)) as client:
            with pytest.raises(CortexTTSError) as caught:
                await client.speak("x", model="hojo-80m-clone", voice=None)
        assert caught.value.code == "MODEL_NOT_READY"
        assert "not downloaded" in str(caught.value)

    async def test_a_non_json_failure_still_carries_a_code(self) -> None:
        async def bad_gateway(_: web.Request) -> web.Response:
            return web.Response(status=502, text="<html>bad gateway")

        async with _client(post_api_speak=bad_gateway) as client:
            with pytest.raises(CortexTTSError) as caught:
                await client.speak("x", model="hojo-40m", voice=None)
        assert caught.value.code == "HTTP_ERROR"


STREAM_HEADERS = {"Content-Type": "audio/mpeg", "X-Cortex-Bitrate": "192000"}
"""What the server sends before a streamed reply. The bitrate is the only
measurement that exists before the first sample, and the client refuses a
stream without it — a chunk whose duration is unknowable cannot be buffered
against."""


class TestSpeakStream:
    """Audio arriving in pieces, which is the point of the endpoint."""

    @pytest.mark.asyncio
    async def test_pieces_arrive_before_the_body_is_finished(self) -> None:
        """Collecting the body first would be a slower `speak` with extra steps.

        The writes are spaced: over loopback an unspaced burst is coalesced
        into one read, and the test would pass or fail on buffer timing rather
        than on whether the client waits for EOF.
        """
        released = asyncio.Event()

        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers=STREAM_HEADERS)
            await response.prepare(request)
            # One chunk straight away, standing in for the frames the server
            # has as soon as the codec produces any. Without it there is
            # nothing for the first `anext` to return and the test deadlocks
            # on its own fixture rather than measuring the client.
            await response.write(b"\x01\x02" * 240)
            for _i in range(3):
                await released.wait()
                await response.write(b"\x01\x02" * 240)
            await response.write_eof()
            return response

        async with _client(post_api_speak_stream=handler) as client:
            stream = client.speak_stream("你好。", model="m", voice="v")
            first = await anext(stream)
            # Nothing after that first chunk has been written yet, so a
            # client that waited for the whole body would still be blocked.
            released.set()
            rest = [piece async for piece in stream]

        pieces = [first, *rest]
        assert len(pieces) > 1, "the whole body arrived as one read"
        assert all(bitrate == 192000 for bitrate, _ in pieces)
        assert sum(len(chunk) for _, chunk in pieces) == 4 * 480

    @pytest.mark.asyncio
    async def test_a_rejected_request_raises_before_any_audio(self) -> None:
        """Once a 200 is out an error can only truncate, so it has to be early."""

        async def handler(_: web.Request) -> web.Response:
            return web.json_response(
                {"code": "NO_VOICE", "message": "no voices"}, status=409
            )

        async with _client(post_api_speak_stream=handler) as client:
            with pytest.raises(CortexTTSError) as err:
                async for _ in client.speak_stream("你好。", model="m", voice=None):
                    pass
        assert err.value.code == "NO_VOICE"

    @pytest.mark.asyncio
    async def test_the_voice_is_omitted_when_none_so_the_server_picks(self) -> None:
        seen: dict[str, object] = {}

        async def handler(request: web.Request) -> web.StreamResponse:
            seen.update(await request.json())
            response = web.StreamResponse(headers=STREAM_HEADERS)
            await response.prepare(request)
            await response.write(b"\xff\xfb")
            await response.write_eof()
            return response

        async with _client(post_api_speak_stream=handler) as client:
            async for _ in client.speak_stream("你好。", model="m", voice=None):
                pass

        assert "voice" not in seen
        assert seen["model"] == "m"
        assert seen["format"] == "mp3", "a WAV stream must declare a length"


class TestStreamBitrate:
    """The client refuses a stream it cannot measure.

    Every buffering decision downstream — the head start, the playback margin,
    the audio-length sensor — is a byte count divided by this. A stream
    without it would be played, and silently mismeasured everywhere.
    """

    @pytest.mark.asyncio
    async def test_a_stream_with_no_bitrate_is_refused(self) -> None:
        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "audio/mpeg"})
            await response.prepare(request)
            await response.write(b"\xff\xfb")
            await response.write_eof()
            return response

        async with _client(post_api_speak_stream=handler) as client:
            with pytest.raises(CortexTTSError) as err:
                async for _ in client.speak_stream("你好。", model="m", voice=None):
                    pass
        assert err.value.code == "malformed_stream"

    @pytest.mark.asyncio
    async def test_a_bitrate_that_is_not_a_number_is_refused(self) -> None:
        async def handler(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                headers={"Content-Type": "audio/mpeg", "X-Cortex-Bitrate": "lots"}
            )
            await response.prepare(request)
            await response.write(b"\xff\xfb")
            await response.write_eof()
            return response

        async with _client(post_api_speak_stream=handler) as client:
            with pytest.raises(CortexTTSError) as err:
                async for _ in client.speak_stream("你好。", model="m", voice=None):
                    pass
        assert err.value.code == "malformed_stream"


class TestApiVersion:
    """The one number a client checks before trusting any other field."""

    async def test_a_server_that_reports_another_version_is_refused(self) -> None:
        async def health(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok", "api_version": 2})

        async with _client(get_health=health) as client:
            assert await client.validate() == "unsupported_api"

    async def test_a_server_too_old_to_report_one_is_version_one(self) -> None:
        async def models(_: web.Request) -> web.Response:
            return web.json_response([])

        async with _client(get_health=_ok, get_api_models=models) as client:
            assert await client.validate() is None


class TestRefusals:
    """A server that answers is not a network problem."""

    async def test_a_rejected_key_is_an_auth_error(self) -> None:
        async def refuse(_: web.Request) -> web.Response:
            return web.json_response(
                {"code": "AUTH_REQUIRED", "message": "authentication required"},
                status=401,
            )

        async with _client(get_api_models=refuse) as client:
            with pytest.raises(CortexTTSAuthError) as caught:
                await client.list_models()
        assert caught.value.code == "AUTH_REQUIRED"
        assert not isinstance(caught.value, aiohttp.ClientError)

    async def test_a_model_without_capability_flags_still_lists(self) -> None:
        """Only id, name, languages and downloaded decide anything here."""

        async def models(_: web.Request) -> web.Response:
            return web.json_response(
                [{"id": "m", "name": "M", "languages": ["zh"], "downloaded": True}]
            )

        async with _client(get_api_models=models) as client:
            (model,) = await client.list_models()
        assert model.downloaded is True
        assert model.cloning is False
