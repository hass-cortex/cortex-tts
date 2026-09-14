"""The TTS entity's decisions, none of which need a server."""

from __future__ import annotations

from custom_components.cortex_tts.tts import CortexTTSEntity, _expand_languages


class TestTextOptions:
    """Which text passes a call runs, when the caller names none.

    This is the rule that was wrong in production: normalisation was gated on
    the language, so an English request sent `normalize_text: false` and the
    model — which pronounces no Arabic numeral at all — said "a bay" where the
    text read "80".
    """

    @staticmethod
    def _options(given: dict[str, object] | None = None) -> dict[str, bool | None]:
        return CortexTTSEntity._text_options(None, given or {})  # type: ignore[arg-type]

    def test_numbers_are_expanded_unless_told_otherwise(self) -> None:
        assert self._options()["normalize_text"] is True
        assert self._options({"normalize_text": False})["normalize_text"] is False

    def test_the_chinese_rewrites_are_left_to_the_server(self) -> None:
        # The server decides both from the language it is sent — conversion
        # for any Chinese, Taiwan readings for Taiwan's — so an unset option
        # travels as absent, never as a default chosen here.
        assert self._options() == {
            "normalize_text": True,
            "expand_numbers": None,
            "convert_script": None,
            "taiwan_readings": None,
        }

    def test_bare_numbers_travel_only_when_asked(self) -> None:
        assert self._options({"expand_numbers": True})["expand_numbers"] is True

    def test_an_explicit_option_travels_as_set(self) -> None:
        assert self._options({"convert_script": False})["convert_script"] is False
        assert self._options({"taiwan_readings": True})["taiwan_readings"] is True
        assert self._options({"taiwan_readings": False})["taiwan_readings"] is False


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
