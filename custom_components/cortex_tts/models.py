"""Typed views of what the Cortex TTS server returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_HEAD_START,
    CONF_STREAM_MODE,
    DEFAULT_HEAD_START,
    MAX_HEAD_START,
    STREAM_BUFFERED,
    STREAM_COALESCED,
    STREAM_MODES,
    STREAM_RTF_CEILING,
    SUBENTRY_TYPE,
)

if TYPE_CHECKING:
    from .client import CortexTTSClient


@dataclass(frozen=True)
class VoiceInfo:
    """A voice offered by one model.

    Attributes:
        id: Identifier to send back in a synthesis request.
        name: Label shown in the Home Assistant voice picker.
        language: Base language code, or ``None`` for a cloned voice that
            carries no declared language.
        gender: ``female``, ``male`` or ``unknown``.
        source: ``builtin`` or ``reference``.
    """

    id: str
    name: str
    language: str | None
    gender: str
    source: str


@dataclass(frozen=True)
class ModelInfo:
    """A model on the server and whether it can be used right now."""

    id: str
    name: str
    description: str
    builtin_voices: bool
    cloning: bool
    chunk_streaming: bool
    languages: list[str]
    sample_rate: int
    downloaded: bool
    loaded: bool
    rtf_hint: float = 0.0
    """The catalog's relative cost figure, 0.0 when the server reports none."""

    @property
    def outruns_playback(self) -> bool:
        """Whether this model synthesises faster than its audio plays.

        An unknown rate counts as too slow: streaming a model that cannot keep
        up stalls mid-reply, so it is not something to assume.
        """
        return 0.0 < self.rtf_hint < STREAM_RTF_CEILING


@dataclass
class SpeechStats:
    """What one synthesis cost, pushed to the diagnostic sensors."""

    success: bool
    characters: int = 0
    audio_seconds: float = 0.0
    inference_ms: float = 0.0
    """What the model cost, as the server measured it. Absent when streaming:
    the measurement headers are gone before the first sample exists."""
    generation_ms: float = 0.0
    """Wall-clock from request to last frame, measured on this side. Not the
    same quantity as `inference_ms` — nothing throttles the read, so for a
    stream the two coincide, but the clock here is a wait and not a cost."""
    rtf: float = 0.0
    first_audio_ms: float = 0.0
    """Request to the first frame of audio — what the listener actually waits."""
    language: str = ""
    voice: str = ""
    margin_seconds: float | None = None
    """The smallest lead the listener ever had, in seconds of audio.

    Starts at the head start and falls whenever rendering is slower than
    playback. Negative means this side ran dry: no downstream buffer could
    have covered it, because the audio did not exist yet. Positive means it
    did not, and a stutter the listener heard came from somewhere else."""
    longest_gap_ms: float | None = None
    """The longest the stream went without sending anything.

    A consumer with a small buffer stutters on this even while the margin
    above stays healthy, so the two answer different questions."""
    mode: str = STREAM_BUFFERED
    """How this reply reached the speaker — one of `STREAM_MODES`. What
    happened, not what was configured: a caller that hands over the whole
    message at once is spoken buffered whatever the model is set to."""


@dataclass
class CortexTTSRuntimeData:
    """Everything a config entry keeps alive while loaded.

    Attributes:
        client: HTTP client bound to the configured server.
        models: Models that are downloaded and therefore entity-worthy.
        voices: Voices keyed by model id.
        sensors_by_model: Diagnostic sensor channels to notify after a
            synthesis, keyed by model id.
    """

    client: CortexTTSClient
    models: list[ModelInfo]
    voices: dict[str, list[VoiceInfo]] = field(default_factory=dict)
    sensors_by_model: dict[str, list] = field(default_factory=dict)


def model_subentry(entry: ConfigEntry, model_id: str) -> ConfigSubentry | None:
    """Return the subentry holding a model's options, if it has one yet.

    Subentries are reconciled against the server's model list, so one can be
    briefly absent — between a model appearing and the reconcile that follows
    — and every caller has to cope with that rather than assume.
    """
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE and subentry.unique_id == model_id:
            return subentry
    return None


def default_stream_mode(model: ModelInfo | None) -> str:
    """How a model speaks until someone chooses for it.

    Coalesced rather than plain sentence streaming, because sending one
    sentence at a time costs a fresh prefill per sentence and buys nothing a
    coalesced stream does not already give — including time to first audio.
    A model that cannot stay ahead of its own audio is not streamed at all.
    """
    if model is not None and model.outruns_playback:
        return STREAM_COALESCED
    return STREAM_BUFFERED


def stream_mode(entry: ConfigEntry, model: ModelInfo) -> str:
    """Return the mode configured for a model, or its default.

    Read at synthesis time rather than cached, so editing a model's subentry
    takes effect on the next reply instead of on the next reload.
    """
    subentry = model_subentry(entry, model.id)
    if subentry is not None:
        configured = subentry.data.get(CONF_STREAM_MODE)
        if configured in STREAM_MODES:
            return str(configured)
    return default_stream_mode(model)


def head_start(entry: ConfigEntry, model: ModelInfo) -> float:
    """Seconds of audio to bank before a streamed reply starts playing.

    Read at synthesis time, like `stream_mode`, and clamped: a value from a
    hand-edited entry must not be able to hold a reply back indefinitely.
    """
    subentry = model_subentry(entry, model.id)
    if subentry is None:
        return DEFAULT_HEAD_START
    try:
        seconds = float(subentry.data.get(CONF_HEAD_START, DEFAULT_HEAD_START))
    except TypeError, ValueError:
        return DEFAULT_HEAD_START
    return min(max(seconds, 0.0), MAX_HEAD_START)
