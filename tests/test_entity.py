"""The device a model's entities share, and the strings users see."""

from __future__ import annotations

import json
import pathlib

from custom_components.cortex_tts.const import DOMAIN
from custom_components.cortex_tts.entity import device_for
from custom_components.cortex_tts.models import ModelInfo

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_COMPONENT = _ROOT / "custom_components" / "cortex_tts"


def _model() -> ModelInfo:
    return ModelInfo(
        id="hojo-40m",
        name="Hojo TTS Light 40M",
        description="15 built-in voices",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=False,
        languages=["zh", "en"],
        sample_rate=24000,
        downloaded=True,
        loaded=True,
    )


def test_the_tts_entity_and_its_sensors_share_one_device() -> None:
    # Two call sites built this identically; they must keep agreeing or the
    # sensors detach from the voice they describe.
    assert device_for("entry1", _model()) == device_for("entry1", _model())


def test_the_device_is_keyed_by_entry_and_model() -> None:
    device = device_for("entry1", _model())
    assert device["identifiers"] == {(DOMAIN, "entry1_hojo-40m")}
    assert device_for("entry2", _model())["identifiers"] != device["identifiers"]


class TestTranslations:
    """strings.json is the source; en.json is what ships."""

    @staticmethod
    def _load(name: str) -> dict:
        return json.loads((_COMPONENT / name).read_text())

    def test_english_matches_the_source_strings(self) -> None:
        assert self._load("strings.json") == self._load("translations/en.json")

    def test_every_sensor_has_a_name(self) -> None:
        from custom_components.cortex_tts.sensor import DESCRIPTIONS

        named = self._load("strings.json")["entity"]["sensor"]
        assert {d.key for d in DESCRIPTIONS} == set(named)
        assert all(entry.get("name") for entry in named.values())


def test_the_manifest_version_matches_the_project() -> None:
    import tomllib

    manifest = json.loads((_COMPONENT / "manifest.json").read_text())
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"]
