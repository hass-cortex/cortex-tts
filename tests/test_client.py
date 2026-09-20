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
    return web.json_response({"status": "ok", "api_version": 5})


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


class TestApiVersion:
    """The one number a client checks before trusting any other field."""

    @pytest.mark.parametrize("version", [4, 6])
    async def test_a_server_that_reports_another_version_is_refused(
        self, version: int
    ) -> None:
        async def health(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok", "api_version": version})

        async with _client(get_health=health) as client:
            assert await client.validate() == "unsupported_api"

    async def test_a_server_too_old_to_report_one_is_refused_too(self) -> None:
        """Version 1 had no live endpoint, and an app that predates the field
        predates that as well."""

        async def old(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async with _client(get_health=old) as client:
            assert await client.validate() == "unsupported_api"

    async def test_the_current_version_is_accepted(self) -> None:
        async def health(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok", "api_version": 5})

        async def models(_: web.Request) -> web.Response:
            return web.json_response([])

        async with _client(get_health=health, get_api_models=models) as client:
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

    async def test_the_servers_own_message_reaches_the_caller(self) -> None:
        """Home Assistant surfaces this string, so it must be the app's own.

        Not "500 Internal Server Error", which says nothing a person can act
        on. `_raise_for_status` is shared by every route that answers JSON.
        """

        async def refuse(_: web.Request) -> web.Response:
            return web.json_response(
                {"code": "MODEL_NOT_READY", "message": "not downloaded"}, status=409
            )

        async with _client(get_api_models=refuse) as client:
            with pytest.raises(CortexTTSError) as caught:
                await client.list_models()
        assert caught.value.code == "MODEL_NOT_READY"
        assert "not downloaded" in str(caught.value)

    async def test_a_non_json_failure_still_carries_a_code(self) -> None:
        """A proxy's HTML error page is still a refusal the caller can name."""

        async def bad_gateway(_: web.Request) -> web.Response:
            return web.Response(status=502, text="<html>bad gateway")

        async with _client(get_api_models=bad_gateway) as client:
            with pytest.raises(CortexTTSError) as caught:
                await client.list_models()
        assert caught.value.code == "HTTP_ERROR"

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
