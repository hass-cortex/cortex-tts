"""What a model is told beyond its voice.

One option — the style instruction — declared per entity rather than per
integration: Home Assistant refuses an option an entity has not declared, so
offering one the server would then reject moves the error away from whoever
wrote the automation.

The language is not an option. Home Assistant already passes one, and it means
the language of the text, which is what the model needs to be told; a second
would have to mean something else, and nothing does.
"""

from __future__ import annotations

from custom_components.cortex_tts.client import _speak_body
from custom_components.cortex_tts.const import CONF_STYLE_INSTRUCTION
from custom_components.cortex_tts.models import ModelInfo


def _model(**flags: bool) -> ModelInfo:
    return ModelInfo(
        id="qwen3-tts-0.6b",
        name="Qwen3-TTS 0.6B (built-in voices)",
        description="Nine built-in speakers",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=True,
        languages=["zh", "en"],
        sample_rate=24000,
        downloaded=True,
        loaded=True,
        **flags,
    )


class TestWhatTheServerIsSent:
    """An unset option is absent, not empty: "" would ask for something."""

    def test_neither_is_sent_when_unset(self) -> None:
        body = _speak_body(
            "hi",
            model="m",
            voice=None,
            normalize_text=True,
            convert_script=False,
            spoken_language=None,
            instruct=None,
        )
        assert "language" not in body
        assert "instruct" not in body

    def test_an_empty_string_is_not_a_request(self) -> None:
        body = _speak_body(
            "hi",
            model="m",
            voice=None,
            normalize_text=True,
            convert_script=False,
            spoken_language="",
            instruct="",
        )
        assert "language" not in body
        assert "instruct" not in body

    def test_both_travel_when_set(self) -> None:
        body = _speak_body(
            "hi",
            model="m",
            voice="vivian",
            normalize_text=True,
            convert_script=False,
            spoken_language="en",
            instruct="say it angrily",
        )
        assert body["language"] == "en"
        assert body["instruct"] == "say it angrily"


class TestCapabilitiesDefaultToOff:
    """An older server reports neither field; the entity must then offer
    neither option rather than send one that would be refused."""

    def test_a_model_without_the_flags_claims_nothing(self) -> None:
        model = _model()
        assert model.language_choice is False
        assert model.style_instruction is False

    def test_the_flags_are_read_when_present(self) -> None:
        model = _model(language_choice=True, style_instruction=True)
        assert model.language_choice
        assert model.style_instruction


class TestTheLanguageComesFromHomeAssistant:
    """One language, not two. The locale is Home Assistant's business."""

    def _entity(self, **flags: bool):
        from custom_components.cortex_tts.tts import CortexTTSEntity

        entity = CortexTTSEntity.__new__(CortexTTSEntity)
        entity._model = _model(**flags)
        return entity

    def test_the_tag_travels_whole(self) -> None:
        """Not reduced to `zh`. Which part of a tag matters is the model's to
        decide, and the server narrows it against that model's own list —
        Qwen3-TTS names Chinese dialects, OmniVoice names Cantonese apart from
        Chinese. Reducing it here would throw that away first."""
        entity = self._entity(language_choice=True)
        assert entity._delivery_options("zh-TW", {})["spoken_language"] == "zh-TW"
        assert entity._delivery_options("en-US", {})["spoken_language"] == "en-US"

    def test_a_model_where_the_voice_decides_is_told_nothing(self) -> None:
        entity = self._entity()
        assert entity._delivery_options("zh-TW", {})["spoken_language"] is None

    def test_the_instruction_still_travels_separately(self) -> None:
        entity = self._entity(language_choice=True, style_instruction=True)
        got = entity._delivery_options("zh-TW", {CONF_STYLE_INSTRUCTION: "angrily"})
        assert got == {"spoken_language": "zh-TW", "instruct": "angrily"}
