"""Which voices an entity offers for a language.

Filtering the list by the pipeline's language was right while every model's
voice decided the language. Two of them now take one as a parameter, and
declare more languages than they ship voices for — Qwen3-TTS reads ten with
nine speakers — so on those a language with no voice of its own must not
empty the picker of an entity that declares it speaks that language.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.cortex_tts.models import ModelInfo, VoiceInfo
from custom_components.cortex_tts.tts import CortexTTSEntity


def _voice(voice_id: str, language: str | None) -> VoiceInfo:
    return VoiceInfo(
        id=voice_id,
        name=voice_id,
        language=language,
        gender="unknown",
        source="builtin" if language else "designed",
    )


def _entity(voices: list[VoiceInfo], *, language_choice: bool) -> CortexTTSEntity:
    model = ModelInfo(
        id="m",
        name="M",
        description="",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=False,
        languages=["zh", "en", "de"],
        sample_rate=24000,
        downloaded=True,
        loaded=True,
        language_choice=language_choice,
    )
    entity = CortexTTSEntity.__new__(CortexTTSEntity)
    entity._model = model
    entity._config_entry = SimpleNamespace(
        runtime_data=SimpleNamespace(voices={"m": voices})
    )
    return entity


def _ids(voices) -> list[str]:
    return [v.voice_id for v in (voices or [])]


class TestTheOrdinaryCase:
    def test_a_matching_language_is_offered(self) -> None:
        entity = _entity(
            [_voice("zh1", "zh"), _voice("en1", "en")], language_choice=False
        )
        assert _ids(entity.async_get_supported_voices("zh-TW")) == ["zh1"]

    def test_a_voice_without_a_language_is_offered_everywhere(self) -> None:
        """A designed voice reads whatever it is given."""
        entity = _entity(
            [_voice("zh1", "zh"), _voice("any", None)], language_choice=False
        )
        assert _ids(entity.async_get_supported_voices("de-DE")) == ["any"]


class TestALanguageWithNoVoiceOfItsOwn:
    def test_a_voice_bound_model_offers_nothing(self) -> None:
        """MOSS cannot be told a language, so it genuinely has no German
        voice; `None` is the honest answer."""
        entity = _entity(
            [_voice("zh1", "zh"), _voice("en1", "en")], language_choice=False
        )
        assert entity.async_get_supported_voices("de-DE") is None

    def test_a_model_that_takes_a_language_offers_all_of_them(self) -> None:
        """The language travels in the request; the speaker is just a timbre."""
        entity = _entity(
            [_voice("zh1", "zh"), _voice("en1", "en")], language_choice=True
        )
        assert _ids(entity.async_get_supported_voices("de-DE")) == ["zh1", "en1"]

    def test_no_voices_at_all_is_still_nothing(self) -> None:
        """A cloning model with no recording uploaded yet — offering the empty
        list as "all of them" would be a picker with nothing in it."""
        entity = _entity([], language_choice=True)
        assert entity.async_get_supported_voices("de-DE") is None
