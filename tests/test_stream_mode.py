"""Which mode a model speaks in, and where that choice lives.

Each model carries its own setting in its own subentry, because the thing
being configured is a property of one model: whether it can stay ahead of its
own audio. The list of "models that stream", which this replaced, was only
ever a column of per-model answers wearing one form.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentry

from custom_components.cortex_tts.const import (
    CONF_STREAM_MODE,
    LEGACY_STREAM_MODES,
    STREAM_AUTO,
    STREAM_BUFFERED,
    STREAM_MODES,
    SUBENTRY_TYPE,
)
from custom_components.cortex_tts.models import (
    ModelInfo,
    default_stream_mode,
    model_subentry,
    stream_mode,
)


def _model(model_id: str = "moss-nano") -> ModelInfo:
    return ModelInfo(
        id=model_id,
        name=model_id,
        description="",
        builtin_voices=True,
        cloning=False,
        chunk_streaming=True,
        languages=["zh"],
        sample_rate=48000,
        downloaded=True,
        loaded=False,
    )


class _Entry:
    """Only the part of a config entry these functions read."""

    def __init__(self, *subentries: ConfigSubentry) -> None:
        self.subentries = {s.subentry_id: s for s in subentries}


def _subentry(model_id: str, **data: Any) -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_TYPE,
        title=model_id,
        unique_id=model_id,
    )


class TestDefault:
    """Auto, whatever the catalog claims about the model.

    Nothing in the catalog can decide this: a speed measured on one host does
    not predict another, and a model that streams comfortably where it was
    measured cannot keep up on a four-core HA VM. Only the server, from what
    it has actually served, knows — so the client asks for `auto` and lets it.
    """

    def test_it_is_auto(self) -> None:
        assert default_stream_mode() == STREAM_AUTO

    def test_it_takes_no_model(self) -> None:
        """No model goes in, because no property of a model decides it."""
        import inspect

        assert not inspect.signature(default_stream_mode).parameters


class TestResolution:
    def test_no_subentry_yet_means_the_default(self) -> None:
        assert stream_mode(_Entry(), _model()) == default_stream_mode()

    def test_an_empty_subentry_means_the_default(self) -> None:
        entry = _Entry(_subentry("moss-nano"))
        assert stream_mode(entry, _model()) == default_stream_mode()

    @pytest.mark.parametrize("mode", STREAM_MODES)
    def test_a_saved_choice_wins(self, mode: str) -> None:
        entry = _Entry(_subentry("moss-nano", **{CONF_STREAM_MODE: mode}))
        assert stream_mode(entry, _model()) == mode

    def test_a_choice_that_is_no_longer_a_mode_is_ignored(self) -> None:
        """A stored value from an older spelling must not disable speech."""
        entry = _Entry(_subentry("moss-nano", **{CONF_STREAM_MODE: "streamed"}))
        assert stream_mode(entry, _model()) in STREAM_MODES

    def test_models_do_not_read_each_other_s_settings(self) -> None:
        entry = _Entry(
            _subentry("moss-nano", **{CONF_STREAM_MODE: STREAM_AUTO}),
            _subentry("hojo-40m", **{CONF_STREAM_MODE: STREAM_BUFFERED}),
        )
        assert stream_mode(entry, _model("moss-nano")) == STREAM_AUTO
        assert stream_mode(entry, _model("hojo-40m")) == STREAM_BUFFERED

    @pytest.mark.parametrize("legacy", sorted(LEGACY_STREAM_MODES))
    def test_the_old_streaming_words_still_mean_streaming(self, legacy: str) -> None:
        """`sentence`, `coalesced`, `planned` and `unheld` were how a person
        asked for a reply to be spoken as it was written; an entry saved then
        must read as `auto`, which lets the app make that choice."""
        entry = _Entry(_subentry("moss-nano", **{CONF_STREAM_MODE: legacy}))
        assert stream_mode(entry, _model()) == STREAM_AUTO

    def test_a_key_an_older_release_stored_has_no_say(self) -> None:
        """`head_start` lived beside the mode once; a subentry still carrying
        it must resolve from the mode alone."""
        entry = _Entry(
            _subentry(
                "moss-nano", **{CONF_STREAM_MODE: STREAM_BUFFERED, "head_start": 2.5}
            )
        )
        assert stream_mode(entry, _model()) == STREAM_BUFFERED


class TestLookup:
    def test_a_subentry_of_another_type_is_not_a_model(self) -> None:
        """Core may add subentries this integration did not."""
        other = ConfigSubentry(
            data=MappingProxyType({}),
            subentry_type="something-else",
            title="x",
            unique_id="moss-nano",
        )
        assert model_subentry(_Entry(other), "moss-nano") is None

    def test_a_missing_model_is_none_not_an_error(self) -> None:
        assert model_subentry(_Entry(), "moss-nano") is None
