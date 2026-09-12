"""One subentry per downloaded model, reconciled against the server.

Which models exist is the server's business — they appear when downloaded and
vanish when deleted — so nothing here is user-added. That makes reconciliation
the whole contract: a model without a subentry has nowhere to keep its
settings, and a subentry without a model is a configuration that a redownload
would silently inherit.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry

from custom_components.cortex_tts import (
    RETIRED_OPTIONS,
    _drop_retired_options,
    _sync_model_subentries,
)
from custom_components.cortex_tts.const import SUBENTRY_TYPE
from custom_components.cortex_tts.models import ModelInfo


def _model(model_id: str, name: str | None = None) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        name=name or model_id,
        description="",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=False,
        languages=["zh"],
        sample_rate=24000,
        downloaded=True,
        loaded=False,
    )


class _Entry:
    def __init__(self, *subentries: ConfigSubentry, **options: Any) -> None:
        self.entry_id = "entry"
        self.subentries = {s.subentry_id: s for s in subentries}
        self.options = options


class _ConfigEntries:
    """The three calls the reconcile makes, recorded rather than performed."""

    def __init__(self, entry: _Entry) -> None:
        self._entry = entry
        self.added: list[ConfigSubentry] = []
        self.removed: list[str] = []
        self.retitled: list[tuple[str, str]] = []

    def async_add_subentry(self, entry: Any, subentry: ConfigSubentry) -> bool:
        self.added.append(subentry)
        entry.subentries[subentry.subentry_id] = subentry
        return True

    def async_remove_subentry(self, entry: Any, subentry_id: str) -> bool:
        self.removed.append(subentry_id)
        entry.subentries.pop(subentry_id)
        return True

    def async_update_entry(self, entry: Any, *, options: Any) -> bool:
        entry.options = options
        self.reoptioned = options
        return True

    def async_update_subentry(
        self, entry: Any, subentry: ConfigSubentry, *, title: str
    ) -> bool:
        self.retitled.append((subentry.unique_id or "", title))
        return True


class _Hass:
    def __init__(self, entry: _Entry) -> None:
        self.config_entries = _ConfigEntries(entry)


def _existing(model_id: str, title: str | None = None, **data: Any) -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_TYPE,
        title=title or model_id,
        unique_id=model_id,
    )


class TestReconcile:
    def test_a_new_model_gets_a_subentry_keyed_by_its_id(self) -> None:
        entry = _Entry()
        hass = _Hass(entry)
        _sync_model_subentries(hass, entry, [_model("hojo-40m", "Hojo 40M")])

        (added,) = hass.config_entries.added
        assert added.unique_id == "hojo-40m"
        assert added.title == "Hojo 40M"
        assert added.subentry_type == SUBENTRY_TYPE

    def test_an_existing_model_is_left_alone(self) -> None:
        """Re-adding would raise on the duplicate unique id; re-titling would
        churn the registry on every models-changed event."""
        entry = _Entry(_existing("hojo-40m"))
        hass = _Hass(entry)
        _sync_model_subentries(hass, entry, [_model("hojo-40m")])

        assert hass.config_entries.added == []
        assert hass.config_entries.retitled == []
        assert hass.config_entries.removed == []

    def test_a_renamed_model_keeps_its_settings(self) -> None:
        """The title follows the server; the data must not be rebuilt."""
        entry = _Entry(_existing("hojo-40m", title="old", stream_mode="buffered"))
        hass = _Hass(entry)
        _sync_model_subentries(hass, entry, [_model("hojo-40m", "Hojo 40M")])

        assert hass.config_entries.retitled == [("hojo-40m", "Hojo 40M")]
        assert hass.config_entries.added == []
        assert hass.config_entries.removed == []

    def test_a_deleted_model_takes_its_subentry_with_it(self) -> None:
        gone = _existing("moss-nano")
        entry = _Entry(_existing("hojo-40m"), gone)
        hass = _Hass(entry)
        _sync_model_subentries(hass, entry, [_model("hojo-40m")])

        assert hass.config_entries.removed == [gone.subentry_id]

    def test_subentries_this_integration_did_not_make_are_untouched(self) -> None:
        foreign = ConfigSubentry(
            data=MappingProxyType({}),
            subentry_type="something-else",
            title="x",
            unique_id="moss-nano",
        )
        entry = _Entry(foreign)
        hass = _Hass(entry)
        _sync_model_subentries(hass, entry, [])

        assert hass.config_entries.removed == []

    def test_reconciling_twice_changes_nothing_the_second_time(self) -> None:
        """It runs on every models-changed event, which the app fires for a
        voice upload too — far more often than the model set actually moves."""
        entry = _Entry()
        hass = _Hass(entry)
        models = [_model("hojo-40m"), _model("moss-nano")]
        _sync_model_subentries(hass, entry, models)
        assert len(hass.config_entries.added) == 2

        _sync_model_subentries(hass, entry, models)
        assert len(hass.config_entries.added) == 2
        assert hass.config_entries.removed == []


class TestRetiredOptions:
    """The entry-level option that per-model settings replaced.

    Nothing reads it now, so leaving it would only mislead the next person
    reading diagnostics into thinking it still decides something.
    """

    def test_it_is_dropped(self) -> None:
        entry = _Entry(stream_models=["hojo-40m"])
        hass = _Hass(entry)
        _drop_retired_options(hass, entry)
        assert entry.options == {}

    def test_options_that_are_still_used_survive(self) -> None:
        entry = _Entry(stream_models=["hojo-40m"], something_else=1)
        hass = _Hass(entry)
        _drop_retired_options(hass, entry)
        assert entry.options == {"something_else": 1}

    def test_an_entry_without_them_is_not_rewritten(self) -> None:
        """Rewriting on every setup would churn storage for nothing."""
        entry = _Entry()
        hass = _Hass(entry)
        _drop_retired_options(hass, entry)
        assert not hasattr(hass.config_entries, "reoptioned")

    def test_the_retired_list_is_not_empty_by_accident(self) -> None:
        """An empty tuple would make every test above pass vacuously."""
        assert RETIRED_OPTIONS
