"""`cortex_tts.list_voices` — the only way an automation can learn a voice id.

Home Assistant lists an engine's voices over the WebSocket command
`tts/engine/voices` and nowhere else, so a dashboard can ask and a script, a
template or a tool-calling agent cannot. Everything here pins the answer they
get instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from custom_components.cortex_tts.const import DOMAIN
from custom_components.cortex_tts.models import ModelInfo, VoiceInfo
from custom_components.cortex_tts.services import _list_voices, _model_of_entity


def _model(model_id: str, name: str) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        name=name,
        description="",
        builtin_voices=True,
        cloning=True,
        chunk_streaming=False,
        languages=["zh"],
        sample_rate=24000,
        downloaded=True,
        loaded=False,
        rtf_hint=0.5,
    )


def _voice(
    voice_id: str, source: str = "builtin", language: str | None = "zh"
) -> VoiceInfo:
    return VoiceInfo(
        id=voice_id,
        name=voice_id.replace("-", " ").title(),
        language=language,
        gender="unknown",
        source=source,
    )


def _hass(
    *,
    models: list[ModelInfo] | None = None,
    voices: dict[str, list[VoiceInfo]] | None = None,
    loaded: bool = True,
) -> MagicMock:
    """A Home Assistant whose only real content is one loaded config entry."""
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
        runtime_data=SimpleNamespace(models=models or [], voices=voices or {}),
    )
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [entry]
    return hass


def _call(hass: MagicMock, **data: object) -> MagicMock:
    call = MagicMock()
    call.hass = hass
    call.data = data
    return call


class TestListing:
    async def test_it_returns_the_id_speak_needs(self) -> None:
        hass = _hass(
            models=[_model("moss-nano", "MOSS-TTS-Nano")],
            voices={"moss-nano": [_voice("Yuewen")]},
        )
        result = await _list_voices(_call(hass))
        assert result == {
            "voices": [
                {
                    "voice": "Yuewen",
                    "name": "Yuewen",
                    "language": "zh",
                    "gender": "unknown",
                    "source": "builtin",
                    "model": "moss-nano",
                    "model_name": "MOSS-TTS-Nano",
                }
            ],
            "count": 1,
        }

    async def test_every_model_by_default(self) -> None:
        """The ids differ in shape per model, which is the reason to ask."""
        hass = _hass(
            models=[_model("hojo-40m", "40M"), _model("moss-nano", "MOSS")],
            voices={
                "hojo-40m": [_voice("hojo_zh_f_01")],
                "moss-nano": [_voice("Yuewen"), _voice("ya-ping", "reference")],
            },
        )
        result = await _list_voices(_call(hass))
        assert [v["voice"] for v in result["voices"]] == [
            "hojo_zh_f_01",
            "Yuewen",
            "ya-ping",
        ]
        assert result["count"] == 3

    async def test_one_model_can_be_singled_out(self) -> None:
        hass = _hass(
            models=[_model("hojo-40m", "40M"), _model("moss-nano", "MOSS")],
            voices={"hojo-40m": [_voice("hojo_zh_f_01")], "moss-nano": [_voice("X")]},
        )
        result = await _list_voices(_call(hass, model="moss-nano"))
        assert [v["voice"] for v in result["voices"]] == ["X"]

    async def test_a_cloned_voice_carries_the_language_it_was_given(self) -> None:
        """Chosen on upload, and what a pipeline filters on."""
        hass = _hass(
            models=[_model("moss-nano", "MOSS")],
            voices={"moss-nano": [_voice("ya-ping", "reference")]},
        )
        voice = (await _list_voices(_call(hass)))["voices"][0]
        assert voice["language"] == "zh"
        assert voice["source"] == "reference"

    async def test_a_voice_that_declares_no_language_is_passed_through(self) -> None:
        """A built-in whose id the bundle's reader could not classify."""
        hass = _hass(
            models=[_model("hojo-40m", "40M")],
            voices={"hojo-40m": [_voice("odd_one", language=None)]},
        )
        assert (await _list_voices(_call(hass)))["voices"][0]["language"] is None

    async def test_a_model_with_no_voices_yet_is_not_an_error(self) -> None:
        hass = _hass(models=[_model("hojo-80m-clone", "80M")], voices={})
        assert await _list_voices(_call(hass, model="hojo-80m-clone")) == {
            "voices": [],
            "count": 0,
        }

    async def test_an_entry_still_loading_is_skipped(self) -> None:
        """`runtime_data` does not exist until setup finishes."""
        hass = _hass(
            models=[_model("moss-nano", "MOSS")],
            voices={"moss-nano": [_voice("Yuewen")]},
            loaded=False,
        )
        assert (await _list_voices(_call(hass)))["count"] == 0


class TestRefusals:
    async def test_an_unknown_model_is_refused_not_answered_empty(self) -> None:
        """An empty list would read as "this model has no voices"."""
        hass = _hass(models=[_model("moss-nano", "MOSS")], voices={})
        with pytest.raises(ServiceValidationError):
            await _list_voices(_call(hass, model="nope"))

    async def test_an_entity_from_another_integration_is_refused(self) -> None:
        hass = _hass(models=[_model("moss-nano", "MOSS")], voices={})
        with pytest.raises(ServiceValidationError):
            await _list_voices(_call(hass, entity_id="tts.google_translate_say"))


class TestModelOfEntity:
    """The unique id is `<domain>_<config entry>_<model>`, not the model.

    Assuming otherwise is a mistake this test exists to keep made once.
    """

    @staticmethod
    def _registry(record: object) -> None:
        import homeassistant.helpers.entity_registry as er

        er.async_get = MagicMock(  # type: ignore[attr-defined]
            return_value=MagicMock(async_get=MagicMock(return_value=record))
        )

    def test_it_strips_the_prefix_the_entity_was_built_with(self) -> None:
        hass = MagicMock()
        self._registry(
            SimpleNamespace(config_entry_id="abc", unique_id=f"{DOMAIN}_abc_moss-nano"),
        )
        assert _model_of_entity(hass, "tts.x") == "moss-nano"

    def test_a_model_id_containing_the_separator_survives(self) -> None:
        """Splitting on "_" would have returned "40m"."""
        hass = MagicMock()
        self._registry(
            SimpleNamespace(config_entry_id="abc", unique_id=f"{DOMAIN}_abc_hojo_40m"),
        )
        assert _model_of_entity(hass, "tts.x") == "hojo_40m"

    def test_an_entity_of_another_integration_has_no_model(self) -> None:
        hass = MagicMock()
        self._registry(SimpleNamespace(config_entry_id="abc", unique_id="other_abc_x"))
        assert _model_of_entity(hass, "tts.x") is None

    def test_an_unregistered_entity_has_no_model(self) -> None:
        hass = MagicMock()
        self._registry(None)
        assert _model_of_entity(hass, "tts.x") is None
