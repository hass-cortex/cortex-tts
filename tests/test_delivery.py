"""Whether a reply actually played smoothly, as a number instead of an ear.

A stutter has two possible authors and only one of them is this integration:
either the audio was not rendered in time — nothing downstream can rescue
that, the samples did not exist — or it was, and what happened after it left
belongs to the consumer. `margin` answers the first, which is the half this
side can do anything about, and reporting it is what stops the next report of
"it sounds choppy" from being a guessing game.

The measurement it has to get right is the moment a chunk arrives late: what
the listener still held then, *not* counting the chunk now arriving.
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

    def test_one_piece_leaves_it_unmeasured(self, monkeypatch) -> None:
        """A reply delivered whole never had a chunk that could arrive late."""
        _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(5.0)
        assert delivery.margin is None

    def test_the_head_start_is_the_opening_lead(self, monkeypatch) -> None:
        """The banked seconds are what the listener holds when the next
        chunk arrives instantly."""
        _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(5.0)
        delivery.sent(1.0)
        assert delivery.margin == 5.0

    def test_rendering_faster_than_playback_never_lowers_it(self, monkeypatch) -> None:
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(1.0)
        for _ in range(10):
            clock.advance(0.5)
            delivery.sent(1.0)
        assert delivery.margin is not None
        assert 0.49 < delivery.margin < 0.51

    def test_rendering_slower_than_playback_eats_the_lead(self, monkeypatch) -> None:
        """The measured case: 5 s of head start against 1.05x for 70 s.

        The head start loses 0.05 s per second of reply, and the second
        each chunk is worth is not held until that chunk lands."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(5.0)
        for _ in range(70):
            clock.advance(1.05)
            delivery.sent(1.0)
        assert delivery.margin is not None
        assert -0.01 < delivery.margin - (5.0 - 0.05 * 70 - 1.0) < 0.01

    def test_it_keeps_the_worst_moment_not_the_last(self, monkeypatch) -> None:
        """A reply that recovers at the end still starved in the middle."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(1.0)
        clock.advance(3.0)
        delivery.sent(1.0)  # the listener ran 2 s dry here
        clock.advance(0.0)
        delivery.sent(5.0)  # recovered
        assert delivery.margin is not None
        assert delivery.margin < 0

    def test_a_late_chunk_does_not_pay_for_its_own_delay(self, monkeypatch) -> None:
        """The production miss, 2026-09-12: 4.28 s banked, a 5.04 s stall,
        then 12.16 s at once. Counting the arriving chunk reported +4.28
        for a listener that had been dry for 0.76 s."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(4.28)
        clock.advance(5.04)
        delivery.sent(12.16)
        assert delivery.margin is not None
        assert -0.77 < delivery.margin < -0.75

    def test_a_stall_the_lead_covers_stays_positive(self, monkeypatch) -> None:
        """The other half of the same pair: a 2.47 s stall against 4.22 s
        banked was heard by nobody, and must not read as a problem."""
        clock = _Clock(monkeypatch)
        delivery = _Delivery()
        delivery.sent(4.22)
        clock.advance(2.47)
        delivery.sent(6.96)
        assert delivery.margin is not None
        assert 1.74 < delivery.margin < 1.76
