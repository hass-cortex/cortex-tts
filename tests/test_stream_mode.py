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
    STREAM_BUFFERED,
    STREAM_COALESCED,
    STREAM_MODES,
    STREAM_SENTENCE,
    SUBENTRY_TYPE,
)
from custom_components.cortex_tts.models import (
    ModelInfo,
    default_stream_mode,
    model_subentry,
    stream_mode,
)


def _model(model_id: str = "moss-nano", rtf_hint: float = 0.35) -> ModelInfo:
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
        rtf_hint=rtf_hint,
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
    def test_a_model_that_keeps_up_coalesces(self) -> None:
        """Not plain sentence streaming: it costs a prefill per sentence and
        buys nothing a coalesced stream does not already give."""
        assert default_stream_mode(_model(rtf_hint=0.21)) == STREAM_COALESCED

    def test_a_model_that_cannot_keep_up_is_not_streamed(self) -> None:
        assert default_stream_mode(_model(rtf_hint=0.79)) == STREAM_BUFFERED

    def test_a_model_that_is_gone_falls_back_to_the_safe_one(self) -> None:
        """The subentry can outlive the model list for an instant."""
        assert default_stream_mode(None) == STREAM_BUFFERED


class TestResolution:
    def test_no_subentry_yet_means_the_default(self) -> None:
        assert stream_mode(_Entry(), _model()) == default_stream_mode(_model())

    def test_an_empty_subentry_means_the_default(self) -> None:
        entry = _Entry(_subentry("moss-nano"))
        assert stream_mode(entry, _model()) == default_stream_mode(_model())

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
            _subentry("moss-nano", **{CONF_STREAM_MODE: STREAM_SENTENCE}),
            _subentry("hojo-40m", **{CONF_STREAM_MODE: STREAM_BUFFERED}),
        )
        assert stream_mode(entry, _model("moss-nano")) == STREAM_SENTENCE
        assert stream_mode(entry, _model("hojo-40m")) == STREAM_BUFFERED


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
