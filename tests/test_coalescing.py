"""Grouping sentences into requests of a size the model renders well.

There is a cliff on either side. One sentence per request makes the server pay
a fresh prefill each time — 0.37 s of dead air per boundary on MOSS-TTS-Nano,
measured at 41% behind playback for a reply of short sentences. One request
for the whole reply removes those, but the model attends over everything it
has generated, so the bill grows with the square of the request: 17.8% behind
for a 55-second story in one go against 6.5% for the same story in two.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from custom_components.cortex_tts.const import MAX_REQUEST_SECONDS
from custom_components.cortex_tts.text import audio_seconds
from custom_components.cortex_tts.tts import _coalesced


async def _from(items: list[str], pause: float = 0.0) -> AsyncGenerator[str]:
    for item in items:
        if pause:
            await asyncio.sleep(pause)
        yield item


async def _drain(source: AsyncGenerator[tuple[str, int]]) -> list[str]:
    return [text async for text, _ in source]


async def _drain_pairs(
    source: AsyncGenerator[tuple[str, int]],
) -> list[tuple[str, int]]:
    return [item async for item in source]


class TestCoalesced:
    async def test_the_text_is_preserved_exactly(self) -> None:
        """Batching may reorder nothing and drop nothing; the server
        re-segments what it is given, so only the characters matter."""
        sentences = ["一。", "二。", "三。", "四。"]
        assert "".join(await _drain(_coalesced(_from(sentences)))) == "".join(sentences)

    async def test_a_writer_that_is_already_finished_becomes_one_request(self) -> None:
        """The measured best case: the whole reply in a single prefill."""
        batches = await _drain(_coalesced(_from(["一。", "二。", "三。"])))
        assert batches == ["一。二。三。"]

    async def test_the_first_sentence_is_not_held_back(self) -> None:
        """Waiting for a second sentence would trade away the whole point."""
        slow = _from(["一。", "二。"], pause=0.05)
        stream = _coalesced(slow)
        first, reply_chars = await asyncio.wait_for(anext(stream), timeout=0.5)
        assert (first, reply_chars) == ("一。", 0)
        await _drain(stream)

    async def test_a_slow_writer_degrades_to_one_at_a_time(self) -> None:
        """Nothing is queued behind a request that is not in flight."""
        batches = await _drain(_coalesced(_from(["一。", "二。", "三。"], pause=0.02)))
        assert batches == ["一。", "二。", "三。"]

    async def test_nothing_in_nothing_out(self) -> None:
        assert await _drain(_coalesced(_from([]))) == []

    async def test_abandoning_the_stream_does_not_leak_the_reader(self) -> None:
        """A media player that hangs up mid-reply must not leave a task
        reading the rest of the message into a queue nobody drains."""

        started = asyncio.Event()

        async def endless() -> AsyncGenerator[str]:
            started.set()
            index = 0
            while True:
                await asyncio.sleep(0.01)
                index += 1
                yield f"{index}。"

        before = len(asyncio.all_tasks())
        stream = _coalesced(endless())
        await anext(stream)
        await started.wait()
        await stream.aclose()
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) <= before


class TestReplyLength:
    """Each batch carries the whole reply's length once the writer is done.

    That is what lets a head start be sized against a length that is known
    rather than guessed — and it has to survive the cap, which turns one long
    reply into several requests without making its total any less knowable.
    """

    async def test_a_finished_writer_reports_the_whole_reply(self) -> None:
        ((_, reply_seconds),) = await _drain_pairs(_coalesced(_from(["一。", "二。"])))
        assert reply_seconds == audio_seconds("一。二。")

    async def test_a_writer_still_going_reports_nothing(self) -> None:
        pairs = await _drain_pairs(
            _coalesced(_from(["一。", "二。", "三。"], pause=0.02))
        )
        assert [length for _, length in pairs[:-1]] == [0, 0]

    async def test_the_length_covers_batches_not_yet_sent(self) -> None:
        """A capped reply is several requests; the length is still the total."""
        sentences = ["甲" * 40 + "。", "乙" * 40 + "。", "丙" * 40 + "。"]
        pairs = await _drain_pairs(_coalesced(_from(sentences)))
        assert len(pairs) > 1, "the cap did not split this"
        assert pairs[0][1] == sum(audio_seconds(s) for s in sentences)


class TestRequestCap:
    """No request may carry more than `MAX_REQUEST_SECONDS` of audio.

    Past that the model's attention over its own output costs more than the
    prefill that batching saves, and the listener waits out the whole of a
    request before any of it arrives.
    """

    async def test_a_long_reply_is_split(self) -> None:
        sentences = ["甲" * 40 + "。"] * 4
        batches = await _drain(_coalesced(_from(sentences)))
        assert len(batches) > 1
        assert all(audio_seconds(b) <= MAX_REQUEST_SECONDS for b in batches)

    async def test_nothing_is_lost_or_reordered_by_the_split(self) -> None:
        sentences = [f"第{i}句。" * 8 for i in range(6)]
        batches = await _drain(_coalesced(_from(sentences)))
        assert "".join(batches) == "".join(sentences)

    async def test_a_short_reply_is_still_one_request(self) -> None:
        """The cap must not undo what coalescing is for."""
        assert await _drain(_coalesced(_from(["一。", "二。", "三。"]))) == [
            "一。二。三。"
        ]

    async def test_an_oversized_piece_goes_alone(self) -> None:
        """Splitting a run-on belongs to the sentence source, not here: a
        piece that arrives over the limit is text nothing could cut, and it
        still has to be spoken rather than dropped."""
        huge = "長" * 200 + "。"
        assert await _drain(_coalesced(_from([huge]))) == [huge]

    async def test_an_oversized_piece_does_not_swallow_the_next(self) -> None:
        huge = "長" * 200 + "。"
        batches = await _drain(_coalesced(_from([huge, "短。"])))
        assert batches == [huge, "短。"]
