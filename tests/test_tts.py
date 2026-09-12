"""The TTS entity's decisions, none of which need a server."""

from __future__ import annotations

import pytest

from custom_components.cortex_tts.tts import CortexTTSEntity, _expand_languages


class TestTextOptions:
    """Which text passes a call runs, when the caller names none.

    This is the rule that was wrong in production: normalisation was gated on
    the language, so an English request sent `normalize_text: false` and the
    model — which pronounces no Arabic numeral at all — said "a bay" where the
    text read "80".
    """

    @staticmethod
    def _options(
        language: str, given: dict[str, object] | None = None
    ) -> dict[str, bool]:
        return CortexTTSEntity._text_options(None, language, given or {})  # type: ignore[arg-type]

    @pytest.mark.parametrize("language", ["en", "en-US", "en-GB", "de", ""])
    def test_numbers_are_expanded_whatever_the_language(self, language: str) -> None:
        assert self._options(language)["normalize_text"] is True

    @pytest.mark.parametrize("language", ["zh", "zh-TW", "zh-Hant", "ZH-tw"])
    def test_chinese_also_converts_the_script(self, language: str) -> None:
        assert self._options(language) == {
            "normalize_text": True,
            "convert_script": True,
        }

    @pytest.mark.parametrize("language", ["en", "en-US", "de"])
    def test_script_conversion_is_off_outside_chinese(self, language: str) -> None:
        # Rewriting glyphs into Simplified is meaningless for a Latin voice.
        assert self._options(language)["convert_script"] is False

    def test_an_explicit_option_beats_the_language(self) -> None:
        assert (
            self._options("zh-TW", {"convert_script": False})["convert_script"] is False
        )
        assert self._options("en", {"convert_script": True})["convert_script"] is True
        assert (
            self._options("zh-TW", {"normalize_text": False})["normalize_text"] is False
        )


class TestExpandLanguages:
    """A model declares base codes; a pipeline offers regional tags."""

    def test_a_base_code_expands_to_its_regions(self) -> None:
        expanded = _expand_languages(["zh"])
        assert "zh" in expanded
        assert "zh-TW" in expanded

    def test_english_expands_too(self) -> None:
        assert "en-US" in _expand_languages(["en"])

    def test_an_unknown_code_survives_unexpanded(self) -> None:
        assert _expand_languages(["xx"]) == ["xx"]
