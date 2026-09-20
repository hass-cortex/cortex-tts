"""The live session, against a real aiohttp WebSocket server.

What is pinned is the wire: the start frame the server receives, the frames
the caller sees in order, an error frame surfacing as the server's own code,
and — the part that costs a model real work when it is wrong — that leaving
early sends `cancel`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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

Script = Callable[[web.WebSocketResponse, dict], Awaitable[None]]


@asynccontextmanager
async def _server(script: Script) -> AsyncIterator[tuple[CortexTTSClient, dict]]:
    """Serve `/api/speak/live` with `script` driving the server side.

    `seen` collects what the server received: the start frame under "start",
    every later frame under "frames", and the request headers under "headers".
    """
    seen: dict = {"frames": []}

    async def live(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        seen["headers"] = dict(request.headers)
        seen["start"] = json.loads(await ws.receive_str())
        await script(ws, seen)
        return ws

    app = web.Application()
    app.router.add_get("/api/speak/live", live)
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession()
    try:
        client = CortexTTSClient(
            str(server.make_url("")).rstrip("/"), "test-key", session
        )
        yield client, seen
    finally:
        await session.close()
        await server.close()


async def _drain_text(ws: web.WebSocketResponse, seen: dict) -> None:
    """Read text frames until `end` (or `cancel`, or a close)."""
    async for message in ws:
        frame = json.loads(message.data)
        seen["frames"].append(frame)
        if frame["type"] in ("end", "cancel"):
            return


async def _ready(ws: web.WebSocketResponse) -> None:
    await ws.send_json(
        {
            "type": "ready",
            "model": "hojo-40m",
            "voice": "v1",
            "bitrate": 128000,
            "sample_rate": 24000,
            "chunk_streaming": False,
            "mode": "streaming",
        }
    )


class TestFraming:
    async def test_the_start_frame_carries_the_request_and_the_mode(self) -> None:
        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await _drain_text(ws, seen)
            await ws.send_json({"type": "done", "mode": "buffered", "batches": 1})

        async with (
            _server(script) as (client, seen),
            client.speak_live(
                model="hojo-40m",
                voice="v1",
                mode="auto",
                convert_script=False,
                spoken_language="zh-TW",
            ) as session,
        ):
            await session.send_text("好了。")
            await session.end()
            frames = [kind async for kind, _ in session.frames()]

        assert seen["headers"]["Authorization"] == "Bearer test-key"
        assert seen["start"] == {
            "type": "start",
            "model": "hojo-40m",
            "voice": "v1",
            "format": "mp3",
            "mode": "auto",
            "convert_script": False,
            "language": "zh-TW",
        }
        assert seen["frames"] == [
            {"type": "text", "text": "好了。"},
            {"type": "end"},
        ]
        assert frames == ["ready", "done"]

    async def test_the_voice_is_omitted_when_none_so_the_server_picks(self) -> None:
        """Sending `voice: null` would ask for a voice named nothing."""

        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await _drain_text(ws, seen)
            await ws.send_json({"type": "done", "mode": "buffered", "batches": 1})

        async with (
            _server(script) as (client, seen),
            client.speak_live(model="m", voice=None, mode="auto") as session,
        ):
            await session.send_text("你好。")
            await session.end()
            [kind async for kind, _ in session.frames()]

        assert "voice" not in seen["start"]
        assert seen["start"]["model"] == "m"

    async def test_audio_arrives_between_ready_and_done_in_order(self) -> None:
        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await _drain_text(ws, seen)
            await ws.send_bytes(b"\x01")
            await ws.send_bytes(b"\x02")
            await ws.send_json({"type": "done", "mode": "streaming", "batches": 2})

        async with (
            _server(script) as (client, _),
            client.speak_live(model="m", voice=None, mode="auto") as session,
        ):
            await session.end()
            received = [(k, p) async for k, p in session.frames()]

        assert received[0][0] == "ready"
        assert received[1:3] == [("audio", b"\x01"), ("audio", b"\x02")]
        assert received[3][0] == "done"
        assert received[3][1]["batches"] == 2

    async def test_an_empty_piece_is_not_sent(self) -> None:
        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await _drain_text(ws, seen)
            await ws.send_json({"type": "done", "mode": "buffered", "batches": 0})

        async with (
            _server(script) as (client, seen),
            client.speak_live(model="m", voice=None, mode="auto") as session,
        ):
            await session.send_text("")
            await session.end()
            async for _ in session.frames():
                pass
        assert seen["frames"] == [{"type": "end"}]


class TestRefusals:
    async def test_an_error_frame_is_the_servers_own_code(self) -> None:
        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await ws.send_json(
                {"type": "error", "code": "UNKNOWN_VOICE", "message": "no voice 'x'"}
            )
            await ws.close(code=1008)

        async with _server(script) as (client, _):
            with pytest.raises(CortexTTSError) as caught:
                async with client.speak_live(model="m", voice="x", mode="auto") as s:
                    async for _ in s.frames():
                        pass
        assert caught.value.code == "UNKNOWN_VOICE"
        assert "no voice" in str(caught.value)

    async def test_a_policy_close_with_no_frame_is_an_auth_error(self) -> None:
        """How a WebSocket says 401: the handshake succeeds, then 1008."""

        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await ws.close(code=1008)

        async with _server(script) as (client, _):
            with pytest.raises(CortexTTSAuthError):
                async with client.speak_live(model="m", voice=None, mode="auto") as s:
                    async for _ in s.frames():
                        pass


class TestLeaving:
    async def test_leaving_early_sends_cancel(self) -> None:
        """A listener that stops reading must stop the render behind it."""
        got_cancel = asyncio.Event()

        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await ws.send_bytes(b"\x01")
            async for message in ws:
                frame = json.loads(message.data)
                seen["frames"].append(frame)
                if frame["type"] == "cancel":
                    got_cancel.set()
                    return

        async with _server(script) as (client, seen):
            async with client.speak_live(model="m", voice=None, mode="auto") as session:
                async for kind, _ in session.frames():
                    if kind == "audio":
                        break  # the consumer went away mid-reply
            # Leaving the block is what sends it; the server confirms receipt.
            await asyncio.wait_for(got_cancel.wait(), 2)
        assert {"type": "cancel"} in seen["frames"]

    async def test_a_finished_reply_sends_no_cancel(self) -> None:
        async def script(ws: web.WebSocketResponse, seen: dict) -> None:
            await _ready(ws)
            await _drain_text(ws, seen)
            await ws.send_json({"type": "done", "mode": "buffered", "batches": 1})
            # Anything after `done` would be a cancel; give it a moment to arrive.
            with contextlib_suppress():
                await asyncio.wait_for(ws.receive(), 0.2)

        async with (
            _server(script) as (client, seen),
            client.speak_live(model="m", voice=None, mode="auto") as session,
        ):
            await session.end()
            async for _ in session.frames():
                pass
        assert {"type": "cancel"} not in seen["frames"]


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(TimeoutError, aiohttp.ClientError)
