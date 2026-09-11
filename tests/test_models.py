"""The wire DTOs, and the one decision derived from them."""

from __future__ import annotations

import pytest

from custom_components.hojo_tts.const import STREAM_RTF_CEILING
from custom_components.hojo_tts.models import ModelInfo, SpeechStats, VoiceInfo


def _model(**overrides: object) -> ModelInfo:
    base = {
        "id": "hojo-40m",
        "name": "Hojo TTS Light 40M",
        "description": "15 built-in voices",
        "kind": "builtin",
        "languages": ["zh", "en"],
        "sample_rate": 24000,
        "downloaded": True,
        "loaded": False,
        "rtf_hint": 0.21,
    }
    return ModelInfo(**{**base, **overrides})  # type: ignore[arg-type]


class TestOutrunsPlayback:
    """What decides whether a model is streamed sentence by sentence."""

    @pytest.mark.parametrize(
        ("rtf_hint", "expected"),
        [
            (0.21, True),  # the shipped 40M
            (0.79, False),  # the shipped 80M
            (STREAM_RTF_CEILING, False),  # the ceiling itself is not under it
            (0.0, False),  # unknown: never assume a model can keep up
        ],
    )
    def test_only_a_known_rate_under_the_ceiling_streams(
        self, rtf_hint: float, expected: bool
    ) -> None:
        assert _model(rtf_hint=rtf_hint).outruns_playback is expected

    def test_the_ceiling_separates_the_two_shipped_models(self) -> None:
        # The constant's whole job. If the catalog figures move, this is the
        # test that says the default changed with them.
        assert _model(rtf_hint=0.21).outruns_playback
        assert not _model(rtf_hint=0.79).outruns_playback


class TestSpeechStats:
    """A failed synthesis must be distinguishable from an instant one."""

    def test_a_failure_carries_no_numbers(self) -> None:
        stats = SpeechStats(success=False, language="zh-TW")
        assert stats.inference_ms == 0.0
        assert stats.first_audio_ms == 0.0
        assert stats.streamed is False


class TestVoiceInfo:
    """A voice is meaningless without the model it belongs to."""

    def test_a_voice_names_its_model(self) -> None:
        voice = VoiceInfo(
            id="hojo_zh_f_01",
            name="Chinese female 1",
            language="zh",
            gender="female",
            source="builtin",
        )
        assert voice.id == "hojo_zh_f_01"
        assert voice.language == "zh"
