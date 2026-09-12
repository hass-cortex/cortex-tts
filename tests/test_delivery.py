"""Whether a reply actually played smoothly, as two numbers instead of an ear.

A stutter has two possible authors and only one of them is this integration.
Either the audio was not rendered in time — nothing downstream can rescue
that, the samples did not exist — or it was rendered in time and the consumer
could not hold what it was given. `margin` answers the first, `longest_gap`
the second, and reporting both is what stops the next report of "it sounds
choppy" from being a guessing game.
"""

from __future__ import annotations

import time

from custom_components.cortex_tts.tts import _Delivery


class _Clock:
    """Drives `time.perf_counter` so a 70-second reply takes no time to test."""

    def __init__(self, monkeypatch: object) -> None:
        self.now = 1000.0
        monkeypatch.setattr(  # type: ignore[attr-defined]
            time, "perf_counter", lambda: self.now
        )

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestMargin:
    def test_nothing_sent_leaves_it_unmeasured(self) -> None:
        """Unknown must not read as zero, which is the worst real value."""
        assert _Delivery().margin is None

    def test_the_first_send_sets_the_opening_lead(self, monkeypatch) -> None:
        """A banked head start is the lead the listener starts with."""
        _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(5.0)
        assert delivery.margin == 5.0

    def test_rendering_faster_than_playback_never_lowers_it(self, monkeypatch) -> None:
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(1.0)
        for _ in range(10):
            clock.advance(0.5)
            delivery.sent(1.0)
        assert delivery.margin == 1.0

    def test_rendering_slower_than_playback_eats_the_lead(self, monkeypatch) -> None:
        """The measured case: 5 s of head start against 1.05x for 70 s."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(5.0)
        for _ in range(70):
            clock.advance(1.05)
            delivery.sent(1.0)
        assert delivery.margin is not None
        assert -0.01 < delivery.margin - (5.0 - 0.05 * 70) < 0.01

    def test_it_keeps_the_worst_moment_not_the_last(self, monkeypatch) -> None:
        """A reply that recovers at the end still starved in the middle."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(1.0)
        clock.advance(3.0)
        delivery.sent(1.0)  # margin -1.0
        clock.advance(0.0)
        delivery.sent(5.0)  # recovered
        assert delivery.margin is not None
        assert delivery.margin < 0


class TestLongestGap:
    def test_a_steady_stream_has_a_small_one(self, monkeypatch) -> None:
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        for _ in range(5):
            delivery.sent(0.5)
            clock.advance(0.1)
        assert delivery.longest_gap < 0.2

    def test_it_finds_the_stall_between_two_requests(self, monkeypatch) -> None:
        """Measured at ~0.3 s per request boundary, and invisible in the margin."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(10.0)
        clock.advance(0.1)
        delivery.sent(1.0)
        clock.advance(0.32)  # a new request's prefill
        delivery.sent(1.0)
        assert 0.31 < delivery.longest_gap < 0.33
        assert delivery.margin is not None
        assert delivery.margin > 0, "the margin cannot see this, which is the point"

    def test_the_first_send_is_not_a_gap(self, monkeypatch) -> None:
        """Otherwise every reply would report its whole head start as a stall."""
        clock = _Clock(monkeypatch)
        clock.advance(30.0)
        delivery = _Delivery()
        delivery.sent(5.0)
        assert delivery.longest_gap == 0.0
