"""Typed views of what the Cortex TTS server returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_STREAM_MODE,
    DOMAIN,
    LEGACY_STREAM_MODES,
    STREAM_AUTO,
    STREAM_BUFFERED,
    STREAM_MODES,
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
        language: Base language code, or ``None`` when the voice declares
            none — a designed voice, which reads whatever it is given. A
            reference recording always declares one, chosen on upload, and it
            describes the recording rather than what the voice may be asked
            to read.
        gender: ``female``, ``male`` or ``unknown``.
        source: ``builtin``, ``designed`` or ``reference``.
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
    language_choice: bool = False
    """Whether the server takes a language for this model, or the voice decides.

    Defaulting to False is the safe direction: the entity then offers no such
    option, and an older server that does not report the field cannot have one
    sent to it."""
    style_instruction: bool = False
    """Whether the server takes a plain-language instruction beside the voice."""


@dataclass
class SpeechStats:
    """What one synthesis cost, pushed to the diagnostic sensors."""

    success: bool
    characters: int = 0
    text: str = ""
    """The reply as it was handed to the app, whole. Carried as an attribute
    rather than a state: a state is capped at 255 characters and a reply is
    often longer."""
    audio_seconds: float = 0.0
    inference_ms: float = 0.0
    """What the model was busy for, as the app measured it — the response
    headers of a whole reply, the `done` frame of a live one. Never this
    side's clock, which spans the writer and the opening hold as well."""
    rtf: float = 0.0
    first_audio_ms: float = 0.0
    """Request to the first frame of audio — what the listener actually waits."""
    load_ms: float = 0.0
    """Time spent making the model resident before this reply could start,
    as the app measured it. Zero when it was already loaded; the whole of a
    long first-audio after an idle unload."""
    writer_ms: float | None = None
    """How long the writer took to finish the reply, as the app measured it
    between the first frame and `end`. The part of `first_audio_ms` that is
    the conversation agent's, not the app's; None for a reply handed over
    whole, which had no writer to wait for."""
    language: str = ""
    voice: str = ""
    margin_seconds: float | None = None
    """The least audio the listener still held, in seconds, as the app
    measured it at the moment each piece of the reply left.

    Negative means the listener ran dry: no downstream buffer could have
    covered it, because the audio did not exist yet. Positive means it did
    not, and a stutter the listener heard came from somewhere else. A reply
    delivered buffered never had a piece that could be late, so it stays None."""
    mode: str = STREAM_BUFFERED
    """How this reply was actually spoken — one of `SPOKEN_MODES`, as the app
    reported it. Not the setting: a model set to `auto` is streamed or
    buffered per reply, and this says which it was. Buffered until the app
    says otherwise, because a reply nothing has been heard of yet has not
    been streamed."""
    batches: int = 0
    """How many requests the app rendered this reply in: one for a buffered
    reply, several for a streamed one. Which is the whole difference between
    the spoken modes in cost, and nothing else records it."""


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


def entity_unique_id(entry_id: str, model_id: str) -> str:
    """The unique id a model's TTS entity is registered under."""
    return f"{DOMAIN}_{entry_id}_{model_id}"


def model_from_unique_id(entry_id: str, unique_id: str) -> str | None:
    """Recover the model id from an entity's unique id, or None.

    Matched against the prefix the entity was built with rather than split on
    the separator, because a model id may contain one.
    """
    prefix = f"{DOMAIN}_{entry_id}_"
    return unique_id.removeprefix(prefix) if unique_id.startswith(prefix) else None


def default_stream_mode() -> str:
    """How a model speaks until someone chooses for it: auto, always.

    Takes no model on purpose: nothing published about a model predicts this
    host. The app measures its own, and `auto` lets that measurement decide.
    """
    return STREAM_AUTO


def stream_mode_setting(stored: object) -> str:
    """The setting a stored value stands for, legacy spellings included.

    Reads one key of a subentry and nothing else, so whatever else an older
    subentry still carries has no say here.
    """
    if stored in STREAM_MODES:
        return str(stored)
    if stored in LEGACY_STREAM_MODES:
        return STREAM_AUTO
    return default_stream_mode()


def stream_mode(entry: ConfigEntry, model: ModelInfo) -> str:
    """Return the mode configured for a model, or its default.

    Read at synthesis time rather than cached, so editing a model's subentry
    takes effect on the next reply instead of on the next reload. A legacy
    spelling — `sentence`, `coalesced`, `planned`, `unheld` — meant "speak it
    as it is written", which is what `auto` asks for.
    """
    subentry = model_subentry(entry, model.id)
    if subentry is None:
        return default_stream_mode()
    return stream_mode_setting(subentry.data.get(CONF_STREAM_MODE))
