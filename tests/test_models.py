"""The wire DTOs, and the one decision derived from them."""

from __future__ import annotations

from custom_components.cortex_tts.const import STREAM_BUFFERED
from custom_components.cortex_tts.models import ModelInfo, SpeechStats, VoiceInfo


def _model(**overrides: object) -> ModelInfo:
    base = {
        "id": "hojo-40m",
        "name": "Hojo TTS Light 40M",
        "description": "15 built-in voices",
        "builtin_voices": True,
        "cloning": False,
        "chunk_streaming": False,
        "languages": ["zh", "en"],
        "sample_rate": 24000,
        "downloaded": True,
        "loaded": False,
        "rtf_hint": 0.21,
    }
    return ModelInfo(**{**base, **overrides})  # type: ignore[arg-type]


class TestSpeechStats:
    """A failed synthesis must be distinguishable from an instant one."""

    def test_a_failure_carries_no_numbers(self) -> None:
        stats = SpeechStats(success=False, language="zh-TW")
        assert stats.inference_ms == 0.0
        assert stats.first_audio_ms == 0.0
        assert stats.mode == STREAM_BUFFERED


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
