"""The HTTP client, against a real aiohttp server.

A stubbed transport would test the stub. These spin up an actual server, so
the status handling, the headers and the bearer token are exercised as they
are in the app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.hojo_tts.client import HojoTTSClient, HojoTTSError

Handler = Callable[[web.Request], object]


@asynccontextmanager
async def _client(**routes: Handler) -> AsyncIterator[HojoTTSClient]:
    """Serve `routes` (keyed `METHOD_path_with_underscores`) to one client."""
    app = web.Application()
    for name, handler in routes.items():
        method, _, path = name.partition("_")
        app.router.add_route(method.upper(), "/" + path.replace("_", "/"), handler)  # type: ignore[arg-type]
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession()
    try:
        yield HojoTTSClient(str(server.make_url("")).rstrip("/"), "test-key", session)
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
            gone = HojoTTSClient(host, "test-key", session)
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
                    "X-Hojo-Inference-Ms": "2283.0",
                    "X-Hojo-Audio-Seconds": "4.22",
                    "X-Hojo-Rtf": "0.541",
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
            with pytest.raises(HojoTTSError) as caught:
                await client.speak("x", model="hojo-80m-clone", voice=None)
        assert caught.value.code == "MODEL_NOT_READY"
        assert "not downloaded" in str(caught.value)

    async def test_a_non_json_failure_still_carries_a_code(self) -> None:
        async def bad_gateway(_: web.Request) -> web.Response:
            return web.Response(status=502, text="<html>bad gateway")

        async with _client(post_api_speak=bad_gateway) as client:
            with pytest.raises(HojoTTSError) as caught:
                await client.speak("x", model="hojo-40m", voice=None)
        assert caught.value.code == "HTTP_ERROR"
