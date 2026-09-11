"""Typed views of what the Hojo TTS server returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .const import STREAM_RTF_CEILING

if TYPE_CHECKING:
    from .client import HojoTTSClient


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
    kind: str
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
    rtf: float = 0.0
    first_audio_ms: float = 0.0
    """Request to the first frame of audio — what the listener actually waits."""
    language: str = ""
    voice: str = ""
    streamed: bool = False
    """Whether the reply was spoken sentence by sentence."""


@dataclass
class HojoTTSRuntimeData:
    """Everything a config entry keeps alive while loaded.

    Attributes:
        client: HTTP client bound to the configured server.
        models: Models that are downloaded and therefore entity-worthy.
        voices: Voices keyed by model id.
        sensors_by_model: Diagnostic sensor channels to notify after a
            synthesis, keyed by model id.
    """

    client: HojoTTSClient
    models: list[ModelInfo]
    voices: dict[str, list[VoiceInfo]] = field(default_factory=dict)
    sensors_by_model: dict[str, list] = field(default_factory=dict)
