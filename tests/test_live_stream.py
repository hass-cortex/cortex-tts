"""The entity's live path, against a fake session: forward, play, report, cancel.

Nothing here touches a socket. The session stand-in records the text it was
handed and replays a scripted set of frames, so what is pinned is the
entity's own behaviour: every piece of the reply reaches the app as written,
`end` follows the last one, the audio frames come out untouched, the `done`
frame becomes the sensors' numbers, and closing the generator early cancels.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from homeassistant.components.tts import TTSAudioRequest

from custom_components.cortex_tts.const import FIRST_AUDIO_FIELDS, TEXT_FIELDS
from custom_components.cortex_tts.models import ModelInfo, SpeechStats
from custom_components.cortex_tts.tts import CortexTTSEntity


class _FakeSession:
    def __init__(self, frames: list[tuple[str, Any]], *, hold_audio: bool = False):
        self.sent: list[str] = []
        self.ended = asyncio.Event()
        self.cancelled = False
        self._frames = frames
        self._hold = hold_audio
        self.release = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def end(self) -> None:
        self.ended.set()

    async def cancel(self) -> None:
        self.cancelled = True

    async def frames(self) -> AsyncIterator[tuple[str, Any]]:
        for kind, payload in self._frames:
            if kind == "done":
                # The real server says how it went only after hearing `end`.
                await self.ended.wait()
            if kind == "audio" and self._hold:
                # A slow render: the frame after this one never comes until
                # released, which is where a consumer gives up.
                yield kind, payload
                await self.release.wait()
                continue
            yield kind, payload


class _FakeClient:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.opened_with: dict[str, Any] = {}

    @asynccontextmanager
    async def speak_live(self, **kwargs: Any) -> AsyncIterator[_FakeSession]:
        self.opened_with = kwargs
        try:
            yield self.session
        finally:
            await self.session.cancel()


class _Sensors:
    def __init__(self) -> None:
        self.pushed: list[tuple[SpeechStats, frozenset[str] | None]] = []
        self.started = 0

    def handle_speech_start(self) -> None:
        self.started += 1

    def handle_speech(self, stats: SpeechStats, fields=None) -> None:
        self.pushed.append((stats, fields))


class _Runtime:
    def __init__(self, sensors: _Sensors) -> None:
        self.sensors_by_model = {"hojo-40m": [sensors]}
        self.voices = {}


class _Entry:
    entry_id = "entry"

    def __init__(self, sensors: _Sensors, mode: str = "auto") -> None:
        self.runtime_data = _Runtime(sensors)
        self.subentries = {}
        self._mode = mode


def _model() -> ModelInfo:
    return ModelInfo(
        id="hojo-40m",
        name="Hojo 40M",
        description="",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=False,
        languages=["zh"],
        sample_rate=24000,
        downloaded=True,
        loaded=True,
    )


def _entity(
    client: _FakeClient, sensors: _Sensors, mode: str = "auto"
) -> CortexTTSEntity:
    entity = CortexTTSEntity(_Entry(sensors, mode), client, _model())  # type: ignore[arg-type]
    entity._stream_mode = lambda: mode  # type: ignore[method-assign]
    return entity


async def _writer(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


def _request(*pieces: str) -> TTSAudioRequest:
    return TTSAudioRequest(
        language="zh-TW", options={"voice": "v1"}, message_gen=_writer(*pieces)
    )


DONE = {
    "type": "done",
    "mode": "streaming",
    "batches": 2,
    "audio_seconds": 6.5,
    "first_audio_ms": 900.0,
    "min_lead_s": 1.25,
    "wall_ms": 7000.0,
}


class TestForwarding:
    async def test_every_piece_reaches_the_app_as_written_then_end(self) -> None:
        session = _FakeSession(
            [("ready", {"mode": "streaming"}), ("audio", b"\x01"), ("done", DONE)]
        )
        client = _FakeClient(session)
        entity = _entity(client, _Sensors())

        out = [
            chunk
            async for chunk in entity._stream_live(_request("好了，", "燈已經打開了。"))
        ]

        assert session.sent == ["好了，", "燈已經打開了。"]
        assert session.ended.is_set()
        assert out == [b"\x01"]
        assert client.opened_with["model"] == "hojo-40m"
        assert client.opened_with["voice"] == "v1"
        assert client.opened_with["mode"] == "auto"
        assert client.opened_with["spoken_language"] == "zh-TW"

    async def test_the_done_frame_becomes_the_sensors_numbers(self) -> None:
        sensors = _Sensors()
        session = _FakeSession([("ready", {}), ("audio", b"\x01"), ("done", DONE)])
        entity = _entity(_FakeClient(session), sensors)

        async for _ in entity._stream_live(_request("一二三。")):
            pass

        assert sensors.started == 1
        final, fields = sensors.pushed[-1]
        assert fields is None
        assert final.mode == "streaming"
        assert final.batches == 2
        assert final.audio_seconds == 6.5
        assert final.margin_seconds == 1.25
        assert final.characters == 4
        # Pushed for the words once the writer finished, once when the first
        # frame arrived, then the totals.
        # Two partial pushes, in whichever order the writer and the first
        # frame finished: the fake's audio can land before the words are out.
        partial = [f for _, f in sensors.pushed if f is not None]
        assert sorted(map(sorted, partial)) == sorted(
            map(sorted, [TEXT_FIELDS, FIRST_AUDIO_FIELDS - {"mode"}])
        )
        words = next(s for s, f in sensors.pushed if f == TEXT_FIELDS)
        assert words.text == "一二三。"
        assert words.characters == 4

    async def test_a_planned_mode_rides_with_the_first_frame(self) -> None:
        """A `batch` frame before the audio says how the reply is delivered,
        so the mode sensor need not wait for the end of a long reply."""
        sensors = _Sensors()
        session = _FakeSession(
            [
                ("ready", {}),
                ("batch", {"index": 1, "mode": "planned"}),
                ("audio", b"\x01"),
                ("done", DONE),
            ]
        )
        entity = _entity(_FakeClient(session), sensors)
        async for _ in entity._stream_live(_request("一二三。")):
            pass
        early = next(s for s, f in sensors.pushed if f == FIRST_AUDIO_FIELDS)
        assert early.mode == "planned"

    async def test_the_first_audio_is_reported_before_the_reply_ends(self) -> None:
        sensors = _Sensors()
        session = _FakeSession([("ready", {}), ("audio", b"\x01"), ("done", DONE)])
        entity = _entity(_FakeClient(session), sensors)
        stream = entity._stream_live(_request("一二三。"))
        await anext(stream)
        early = [f for _, f in sensors.pushed if f is not None]
        assert early, "the wait was not reported when the first frame arrived"
        assert "first_audio_ms" in early[0]
        assert "mode" not in early[0], "the mode is the app's to report, at the end"
        async for _ in stream:
            pass

    async def test_a_reply_with_no_audio_reports_nothing(self) -> None:
        sensors = _Sensors()
        done = {**DONE, "audio_seconds": 0.0, "batches": 0}
        session = _FakeSession([("ready", {}), ("done", done)])
        entity = _entity(_FakeClient(session), sensors)
        out = [c async for c in entity._stream_live(_request("……"))]
        assert out == []
        # The words were still written and are still worth showing; nothing
        # about audio is.
        assert [f for _, f in sensors.pushed] == [TEXT_FIELDS]


class TestLeaving:
    async def test_closing_the_generator_cancels_the_session(self) -> None:
        """Home Assistant stops reading; the app must hear it."""
        session = _FakeSession(
            [("ready", {}), ("audio", b"\x01"), ("audio", b"\x02"), ("done", DONE)],
            hold_audio=True,
        )
        entity = _entity(_FakeClient(session), _Sensors())
        stream = entity._stream_live(_request("一。", "二。"))
        assert await anext(stream) == b"\x01"
        await stream.aclose()
        assert session.cancelled
        session.release.set()

    async def test_a_refusal_is_a_home_assistant_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.cortex_tts.client import CortexTTSError

        class _Refusing(_FakeSession):
            async def frames(self):
                raise CortexTTSError("no voice", code="UNKNOWN_VOICE")
                yield  # pragma: no cover - makes this an async generator

        sensors = _Sensors()
        entity = _entity(_FakeClient(_Refusing([])), sensors)
        entity._config_entry.async_start_reauth = lambda hass: None  # type: ignore[attr-defined]
        with pytest.raises(HomeAssistantError) as caught:
            async for _ in entity._stream_live(_request("一。")):
                pass
        assert caught.value.translation_key == "synthesis_failed"
        assert sensors.pushed[-1][0].success is False
