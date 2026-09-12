"""Translations, checked against the code that names them.

A missing key is silent in the worst way: Home Assistant renders the raw
`stream_mode` slug, or an enum sensor shows a state nobody wrote a word for,
and nothing logs. These pin the joins that no other test crosses — and the
handful of hassfest rules for a subentry flow, which otherwise surface in CI
minutes later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from custom_components.cortex_tts.config_flow import ModelSubentryFlow
from custom_components.cortex_tts.const import CONF_STREAM_MODE, STREAM_MODES
from custom_components.cortex_tts.sensor import DESCRIPTIONS

ROOT = Path(__file__).resolve().parent.parent / "custom_components/cortex_tts"
STRINGS = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))


def test_the_shipped_english_matches_strings_json() -> None:
    """They are two copies of one file; only the second one is served."""
    english = json.loads((ROOT / "translations/en.json").read_text(encoding="utf-8"))
    assert english == STRINGS


class TestSubentryFlow:
    """hassfest's rules for a subentry flow, checked here instead of in CI."""

    def test_it_declares_an_entry_type_and_a_way_to_start(self) -> None:
        model = STRINGS["config_subentries"]["model"]
        assert model["entry_type"]
        assert model["initiate_flow"]["user"]

    def test_it_carries_no_top_level_title(self) -> None:
        """`flow_title=REMOVED`: hassfest rejects one."""
        assert "title" not in STRINGS["config_subentries"]["model"]

    def test_every_abort_reason_the_flow_raises_has_a_sentence(self) -> None:
        source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
        start = source.index("class ModelSubentryFlow")
        reasons = {
            line.split('reason="')[1].split('"')[0]
            for line in source[start:].splitlines()
            if 'reason="' in line
        }
        assert reasons
        assert reasons <= set(STRINGS["config_subentries"]["model"]["abort"])

    def test_the_reason_core_supplies_for_a_saved_form_has_one_too(self) -> None:
        """`async_update_and_abort` aborts with a reason the flow never names.

        Core defaults it to `reconfigure_successful`, so nothing in this file
        mentions it and the dialog rendered the raw key — reported from the UI
        as the words "reconfigure_successful" on a successful save.
        """
        source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
        start = source.index("class ModelSubentryFlow")
        assert "async_update_and_abort" in source[start:]
        assert (
            "reconfigure_successful" in STRINGS["config_subentries"]["model"]["abort"]
        )

    def test_the_step_the_flow_shows_is_the_step_that_is_translated(self) -> None:
        assert hasattr(ModelSubentryFlow, "async_step_reconfigure")
        assert "reconfigure" in STRINGS["config_subentries"]["model"]["step"]

    def test_the_field_has_a_label(self) -> None:
        step = STRINGS["config_subentries"]["model"]["step"]["reconfigure"]
        assert CONF_STREAM_MODE in step["data"]


class TestServiceWords:
    """`services.yaml` declares the fields; `strings.json` names them.

    Two files, one list, and neither imports the other: a field renamed in one
    renders in the UI as its raw slug, with no label and no description.
    """

    SERVICES = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))

    @pytest.mark.parametrize("service", SERVICES)
    def test_every_service_and_field_has_words(self, service: str) -> None:
        described = STRINGS["services"][service]
        assert described["name"] and described["description"]
        for field in self.SERVICES[service].get("fields", {}):
            assert described["fields"][field]["name"]
            assert described["fields"][field]["description"]

    @pytest.mark.parametrize("service", SERVICES)
    def test_no_field_string_is_left_over(self, service: str) -> None:
        """A removed field leaves a label behind, describing nothing."""
        declared = set(self.SERVICES[service].get("fields", {}))
        assert set(STRINGS["services"][service]["fields"]) == declared

    def test_every_refusal_the_service_raises_has_a_sentence(self) -> None:
        """Otherwise the dialog shows the raw key, and nothing logs."""
        source = (ROOT / "services.py").read_text(encoding="utf-8")
        raised = set(re.findall(r'translation_key="([^"]+)"', source))
        assert raised
        assert raised <= set(STRINGS["exceptions"])


class TestStreamModeWords:
    def test_the_selector_names_every_mode_and_no_others(self) -> None:
        """A mode with no word renders as its slug in the picker."""
        offered = STRINGS["selector"][CONF_STREAM_MODE]["options"]
        assert set(offered) == set(STREAM_MODES)

    def test_the_sensor_names_every_mode_it_can_report(self) -> None:
        states = STRINGS["entity"]["sensor"]["mode"]["state"]
        assert set(states) == set(STREAM_MODES)


class TestSensorNames:
    @pytest.mark.parametrize(
        "key", [description.translation_key for description in DESCRIPTIONS]
    )
    def test_every_sensor_has_a_name(self, key: str | None) -> None:
        assert key in STRINGS["entity"]["sensor"]

    def test_no_sensor_string_is_left_over(self) -> None:
        """A renamed key leaves the old word behind, describing nothing."""
        described = {description.translation_key for description in DESCRIPTIONS}
        assert set(STRINGS["entity"]["sensor"]) == described


def test_every_sensor_icon_names_a_sensor_that_exists() -> None:
    """icons.json is keyed by translation key; a stale key is silently unused."""
    import json
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "custom_components/cortex_tts"
    icons = json.loads((base / "icons.json").read_text())
    strings = json.loads((base / "strings.json").read_text())
    sensors = strings["entity"]["sensor"]
    for key, icon in icons["entity"]["sensor"].items():
        assert key in sensors, f"icons.json names sensor {key!r} which has no strings"
        for state in icon.get("state", {}):
            assert state in sensors[key].get("state", {}), (
                f"icon state {state!r} is not a state of sensor {key!r}"
            )
