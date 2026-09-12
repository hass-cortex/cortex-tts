"""Sizing a request in seconds, and cutting only where a reader would pause.

The measurements behind both halves come from production: the speaking rates
from the Hojo 40M, the shapes from 565 replies the voice pipeline actually
spoke. What that corpus says is that cutting at clause marks alone is always
enough — the longest run without one is 8.4 s — so the character wrap at the
bottom of the module is a bound on damage, not a working path.
"""

from __future__ import annotations

import pytest

from custom_components.cortex_tts.const import MAX_REQUEST_SECONDS
from custom_components.cortex_tts.text import audio_seconds, deliverable


class TestAudioSeconds:
    def test_nothing_takes_no_time(self) -> None:
        assert audio_seconds("") == 0.0

    def test_chinese_reads_at_the_measured_rate(self) -> None:
        """20 characters measured as 4.50 s on the 40M."""
        assert 4.3 < audio_seconds("今天台北白天多雲，氣溫二十六到三十一度。") < 5.3

    def test_latin_reads_far_faster_per_character(self) -> None:
        """116 characters measured as 7.48 s: a character-only limit would
        treat this as three times the request it is."""
        line = (
            "Over the past twenty-four hours, the bedroom temperature stayed "
            "mostly between twenty-five and twenty-eight degrees."
        )
        assert 7.0 < audio_seconds(line) < 8.6
        assert audio_seconds(line) < audio_seconds("今" * len(line))

    def test_a_mixed_sentence_counts_each_script(self) -> None:
        mixed = "今天 26 到 31 度，UV index is extreme，中午前後避開直曬。"
        assert audio_seconds("今天到度中午前後避開直曬") < audio_seconds(mixed)
        assert audio_seconds(mixed) < audio_seconds("今" * len(mixed))


class TestDeliverable:
    def test_a_normal_sentence_is_left_alone(self) -> None:
        """The 97% case: no cut, no punctuation moved, nothing to explain."""
        sentence = "下午三點過後中南部有局部雷陣雨，出門記得帶傘。"
        assert deliverable(sentence, MAX_REQUEST_SECONDS) == [sentence]

    def test_a_run_on_is_cut_at_its_clause_marks(self) -> None:
        """The measured worst case: an enumeration with no full stop in it."""
        sentence = (
            "你房間裡主要有入口燈、大燈、小燈、螢幕掛燈、浴室燈、"
            + "檯燈、" * 40
            + "窗簾。"
        )
        pieces = deliverable(sentence, MAX_REQUEST_SECONDS)
        assert len(pieces) > 1
        assert all(audio_seconds(p) <= MAX_REQUEST_SECONDS for p in pieces)
        assert all(p.endswith(("、", "。")) for p in pieces)

    def test_nothing_is_lost_or_reordered(self) -> None:
        sentence = "一，二，三，" * 40 + "結束。"
        assert "".join(deliverable(sentence, MAX_REQUEST_SECONDS)) == sentence

    def test_a_newline_is_a_cut_the_reply_already_made(self) -> None:
        listed = "待辦：\n" + "".join(f"收衣服倒垃圾買牛奶第{i}項\n" for i in range(30))
        pieces = deliverable(listed, MAX_REQUEST_SECONDS)
        assert len(pieces) > 1
        assert all(p.endswith(("\n", "：")) for p in pieces)

    def test_latin_is_cut_at_a_word_gap_when_it_must_be(self) -> None:
        """No clause mark anywhere, so the wrap runs — and still never lands
        inside a word."""
        line = "the bedroom temperature stayed mostly between values " * 12
        pieces = deliverable(line, MAX_REQUEST_SECONDS)
        assert len(pieces) > 1
        assert all(audio_seconds(p) <= MAX_REQUEST_SECONDS for p in pieces)
        assert "".join(pieces).replace(" ", "") == line.replace(" ", "")

    @pytest.mark.parametrize("limit", [1.0, 4.0, 9.0, 14.0])
    def test_no_piece_ever_exceeds_the_limit(self, limit: float) -> None:
        """Including the pathological input: one word longer than the limit
        has nowhere to go and comes back whole, but nothing else grows."""
        sentence = "無標點的長句" * 60
        pieces = deliverable(sentence, limit)
        assert all(audio_seconds(p) <= limit for p in pieces)
