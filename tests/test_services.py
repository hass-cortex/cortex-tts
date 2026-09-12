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
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from custom_components.cortex_tts.const import DOMAIN
from custom_components.cortex_tts.models import ModelInfo, VoiceInfo
from custom_components.cortex_tts.services import (
    LIST_VOICES_SCHEMA,
    _list_voices,
    _target_of_entity,
)


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


ENTRY_ID = "entry-a"


def _entry(
    entry_id: str,
    models: list[ModelInfo] | None = None,
    voices: dict[str, list[VoiceInfo]] | None = None,
    loaded: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id=entry_id,
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
        runtime_data=SimpleNamespace(models=models or [], voices=voices or {}),
    )


def _hass(
    *,
    models: list[ModelInfo] | None = None,
    voices: dict[str, list[VoiceInfo]] | None = None,
    loaded: bool = True,
    entries: list[SimpleNamespace] | None = None,
) -> MagicMock:
    """A Home Assistant whose only real content is its config entries."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = entries or [
        _entry(ENTRY_ID, models, voices, loaded)
    ]
    return hass


def _registry(records: dict[str, object]) -> None:
    """Point the entity registry at records keyed by entity id."""
    import homeassistant.helpers.entity_registry as er

    er.async_get = MagicMock(  # type: ignore[attr-defined]
        return_value=MagicMock(async_get=MagicMock(side_effect=records.get))
    )


def _tts_record(entry_id: str, model_id: str) -> SimpleNamespace:
    """What the registry holds for a model's TTS entity."""
    return SimpleNamespace(
        config_entry_id=entry_id, unique_id=f"{DOMAIN}_{entry_id}_{model_id}"
    )


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

    async def test_an_entity_narrows_the_answer_to_its_own_model(self) -> None:
        hass = _hass(
            models=[_model("hojo-40m", "40M"), _model("moss-nano", "MOSS")],
            voices={"hojo-40m": [_voice("hojo_zh_f_01")], "moss-nano": [_voice("X")]},
        )
        _registry({"tts.moss": _tts_record(ENTRY_ID, "moss-nano")})
        result = await _list_voices(_call(hass, entity_id="tts.moss"))
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
        _registry({"tts.clone": _tts_record(ENTRY_ID, "hojo-80m-clone")})
        assert await _list_voices(_call(hass, entity_id="tts.clone")) == {
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


class TestTwoServers:
    """A model id names a model on one server, and servers differ.

    Both can offer `moss-nano`, but a cloned voice exists only on the server
    the recording was uploaded to — so a voice from the other one would be
    refused by the very entity that was named here.
    """

    @staticmethod
    def _two() -> MagicMock:
        return _hass(
            entries=[
                _entry(
                    "entry-a",
                    [_model("moss-nano", "MOSS")],
                    {"moss-nano": [_voice("Yuewen"), _voice("ya-ping", "reference")]},
                ),
                _entry(
                    "entry-b",
                    [_model("moss-nano", "MOSS")],
                    {"moss-nano": [_voice("Yuewen"), _voice("kai", "reference")]},
                ),
            ]
        )

    async def test_an_entity_answers_for_its_own_server_only(self) -> None:
        hass = self._two()
        _registry({"tts.moss_a": _tts_record("entry-a", "moss-nano")})
        result = await _list_voices(_call(hass, entity_id="tts.moss_a"))
        assert [v["voice"] for v in result["voices"]] == ["Yuewen", "ya-ping"]

    async def test_naming_nothing_still_answers_for_all_of_them(self) -> None:
        """The question is then about the installation, not about one entity."""
        result = await _list_voices(_call(self._two()))
        assert result["count"] == 4


class TestRefusals:
    async def test_an_entity_from_another_integration_is_refused(self) -> None:
        hass = _hass(models=[_model("moss-nano", "MOSS")], voices={})
        _registry({})
        with pytest.raises(ServiceValidationError):
            await _list_voices(_call(hass, entity_id="tts.google_translate_say"))

    async def test_an_entity_whose_server_is_not_loaded_is_refused(self) -> None:
        """Not answered empty: the server is down, the model is not gone."""
        hass = _hass(models=[_model("moss-nano", "MOSS")], voices={}, loaded=False)
        _registry({"tts.moss": _tts_record(ENTRY_ID, "moss-nano")})
        with pytest.raises(ServiceValidationError):
            await _list_voices(_call(hass, entity_id="tts.moss"))

    async def test_a_model_its_server_dropped_is_refused_not_answered_empty(
        self,
    ) -> None:
        """An empty list would read as "this model has no voices"."""
        hass = _hass(models=[_model("moss-nano", "MOSS")], voices={})
        _registry({"tts.gone": _tts_record(ENTRY_ID, "deleted-model")})
        with pytest.raises(ServiceValidationError):
            await _list_voices(_call(hass, entity_id="tts.gone"))


class TestSchema:
    def test_only_a_tts_entity_is_accepted(self) -> None:
        """A sensor's unique id is `…_<model>_<key>`: its tail is not a model."""
        assert LIST_VOICES_SCHEMA({"entity_id": "tts.moss"})
        with pytest.raises(vol.Invalid):
            LIST_VOICES_SCHEMA({"entity_id": "sensor.moss_playback_margin"})

    def test_no_argument_at_all_is_the_documented_way_to_ask_for_everything(
        self,
    ) -> None:
        assert LIST_VOICES_SCHEMA({}) == {}


class TestTargetOfEntity:
    """The unique id is `<domain>_<config entry>_<model>`, not the model.

    Assuming otherwise is a mistake this test exists to keep made once.
    """

    def test_it_strips_the_prefix_the_entity_was_built_with(self) -> None:
        hass = MagicMock()
        _registry({"tts.x": _tts_record("abc", "moss-nano")})
        assert _target_of_entity(hass, "tts.x") == ("abc", "moss-nano")

    def test_a_model_id_containing_the_separator_survives(self) -> None:
        """Splitting on "_" would have returned "40m"."""
        hass = MagicMock()
        _registry({"tts.x": _tts_record("abc", "hojo_40m")})
        assert _target_of_entity(hass, "tts.x") == ("abc", "hojo_40m")

    def test_an_entity_of_another_integration_has_no_model(self) -> None:
        hass = MagicMock()
        _registry(
            {"tts.x": SimpleNamespace(config_entry_id="abc", unique_id="other_abc_x")}
        )
        assert _target_of_entity(hass, "tts.x") is None

    def test_an_unregistered_entity_has_no_model(self) -> None:
        hass = MagicMock()
        _registry({})
        assert _target_of_entity(hass, "tts.x") is None
