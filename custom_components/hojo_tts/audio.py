"""WAV framing for sentence-at-a-time streaming.

Home Assistant plays a streamed response as one file, so the sentences cannot
each arrive carrying their own RIFF header. The stream is instead a single
header declaring an unknown length followed by the raw frames of every
sentence — the shape the Wyoming integration uses for the same reason.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass


@dataclass(frozen=True)
class PcmFormat:
    """Sample format every chunk of one stream has to share."""

    rate: int
    width: int
    channels: int


def split_wav(payload: bytes) -> tuple[PcmFormat, bytes]:
    """Return the sample format and the raw frames of a WAV file."""
    with io.BytesIO(payload) as buffer, wave.open(buffer, "rb") as wav:
        fmt = PcmFormat(wav.getframerate(), wav.getsampwidth(), wav.getnchannels())
        return fmt, wav.readframes(wav.getnframes())


def stream_header(fmt: PcmFormat) -> bytes:
    """Return a WAV header announcing a stream of unknown length."""
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setframerate(fmt.rate)
            wav.setsampwidth(fmt.width)
            wav.setnchannels(fmt.channels)
        return buffer.getvalue()


def duration_seconds(fmt: PcmFormat, frames: bytes) -> float:
    """Return how long a block of raw frames plays for."""
    bytes_per_frame = fmt.width * fmt.channels * fmt.rate
    return len(frames) / bytes_per_frame if bytes_per_frame else 0.0
