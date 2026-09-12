"""Banking the opening of a stream, and not overpaying for a short reply.

MOSS-TTS-Nano renders at about 1.045x real time on the development host: a
little slower than playback, every second, for the whole reply. The player
therefore loses ground it never wins back — 1.8 s of it over a forty-second
bulletin. Handing it the difference up front is the only fix the integration
can make; the audio genuinely is not ready any sooner.

The cost is time to first audio, so a reply too short to lose anything must
not pay it.
"""

from __future__ import annotations

import pytest

from custom_components.cortex_tts.const import (
    ASSUMED_DEFICIT,
    CONF_HEAD_START,
    DEFAULT_HEAD_START,
    MAX_HEAD_START,
)
from custom_components.cortex_tts.tts import _HeadStart

FRAME = b"\xff\xfb" + b"\x01\x02" * 100
"""A chunk of MP3. What it contains does not matter here — the bank measures
seconds, which the client derives from the byte count and the bitrate."""


class TestNoBank:
    """Zero must be byte-for-byte what the integration did before."""

    def test_everything_goes_straight_out(self) -> None:
        bank = _HeadStart(0.0)
        assert bank.feed(FRAME, 0.5) == [FRAME]

    def test_there_is_nothing_left_to_flush(self) -> None:
        bank = _HeadStart(0.0)
        bank.feed(FRAME, 0.5)
        assert bank.flush() == []


class TestBanking:
    def test_nothing_leaves_until_the_bank_fills(self) -> None:
        bank = _HeadStart(1.0)
        assert bank.feed(FRAME, 0.4) == []
        assert bank.feed(FRAME, 0.4) == []

    def test_the_release_carries_everything_in_order(self) -> None:
        """MP3 frames only decode in the order they were encoded."""
        bank = _HeadStart(1.0)
        bank.feed(b"a", 0.5)
        assert bank.feed(b"b", 0.6) == [b"a", b"b"]

    def test_it_does_not_bank_twice(self) -> None:
        """Once open, the stream stays open for the rest of the reply."""
        bank = _HeadStart(0.5)
        bank.feed(FRAME, 0.6)
        assert bank.feed(b"next", 0.5) == [b"next"]

    def test_a_reply_shorter_than_the_bank_is_released_by_the_flush(self) -> None:
        """Otherwise a short reply would be banked and never spoken at all."""
        bank = _HeadStart(5.0)
        bank.feed(FRAME, 0.5)
        assert bank.flush() == [FRAME]


class TestShortenFor:
    """A reply whose length is known pays only what that length can lose."""

    @staticmethod
    def _needed(seconds: float) -> float:
        return ASSUMED_DEFICIT * seconds

    def test_a_one_line_answer_barely_waits(self) -> None:
        """The case that made this necessary: two seconds for "the light is on"."""
        bank = _HeadStart(2.0)
        bank.shorten_for(2.4)
        assert bank.feed(FRAME, self._needed(2.4) + 0.01) != []

    def test_a_long_reply_keeps_the_whole_bank(self) -> None:
        bank = _HeadStart(2.0)
        bank.shorten_for(39.6)  # the measured bulletin: it needs 3.9s, capped at 2
        assert bank.feed(FRAME, 1.9) == []
        assert bank.feed(FRAME, 0.2) != []

    def test_it_never_raises_the_bank(self) -> None:
        """The setting is a ceiling; an estimate must not climb over it."""
        bank = _HeadStart(0.5)
        bank.shorten_for(100_000)
        assert bank.feed(FRAME, 0.6) != []

    def test_it_accounts_for_what_is_already_banked(self) -> None:
        """Called after audio has arrived, it must not ask for that twice."""
        bank = _HeadStart(5.0)
        bank.feed(FRAME, 1.0)
        bank.shorten_for(1.2 / ASSUMED_DEFICIT)
        assert bank.feed(FRAME, 0.3) != []

    def test_a_reply_already_long_enough_opens_at_once(self) -> None:
        bank = _HeadStart(5.0)
        bank.feed(FRAME, 3.0)
        bank.shorten_for(1.0)
        assert bank.feed(FRAME, 0.0) != []


class TestSetting:
    def test_the_default_changes_nothing_for_anyone(self) -> None:
        """Opt-in: the right value is a property of the host, not the model."""
        assert DEFAULT_HEAD_START == 0.0

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [(2.0, 2.0), (0, 0.0), (-5, 0.0), (MAX_HEAD_START + 50, MAX_HEAD_START)],
    )
    def test_a_stored_value_is_clamped(self, stored: float, expected: float) -> None:
        from types import MappingProxyType

        from homeassistant.config_entries import ConfigSubentry

        from custom_components.cortex_tts.const import SUBENTRY_TYPE
        from custom_components.cortex_tts.models import ModelInfo, head_start

        subentry = ConfigSubentry(
            data=MappingProxyType({CONF_HEAD_START: stored}),
            subentry_type=SUBENTRY_TYPE,
            title="m",
            unique_id="m",
        )

        class _Entry:
            subentries = {subentry.subentry_id: subentry}

        model = ModelInfo(
            id="m",
            name="m",
            description="",
            builtin_voices=True,
            cloning=False,
            chunk_streaming=True,
            languages=["zh"],
            sample_rate=48000,
            downloaded=True,
            loaded=False,
        )
        assert head_start(_Entry(), model) == expected  # type: ignore[arg-type]
