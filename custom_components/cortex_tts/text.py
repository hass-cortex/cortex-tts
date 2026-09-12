"""How long a piece of text takes to say, and where it may be cut.

A request is sized by the seconds of audio it will produce, not by its
characters: the same 61 characters are fourteen seconds of Chinese and four
of English, and a limit in characters is three different limits depending on
what the reply happens to be written in.

Cutting is the other half. A piece only ever splits where a reader would
already pause — a line break, a clause mark, a comma — because every piece
becomes its own request and is given sentence-final punctuation before the
model sees it, so a cut lands in the audio as a full stop. Measured over 565
production replies: cutting only there leaves no piece longer than 8.4 s, so
at any limit from nine seconds up the last-resort wrap below never runs.
"""

from __future__ import annotations

import re

from .const import CHARS_PER_SECOND, LATIN_CHARS_PER_SECOND

_CJK = re.compile(r"[　-鿿豈-﫿＀-￯]")

# Where a cut is allowed, keeping the mark with the text before it. Newlines
# and clause marks are treated alike: both are places the reply itself had
# already stopped.
_BREAKS = re.compile(r"(?<=[\n；;：:，、,])")

# For a run of Latin with no clause mark in it at all, a space is still a word
# gap. Chinese has none, so the character cut below is the floor.
_WORD = " "


def audio_seconds(text: str) -> float:
    """Estimate how long this text takes to speak, in seconds.

    Both rates are measured on the Hojo 40M in production: 108 characters of
    Chinese as 26.6 s, 309 of Latin as 21.1 s. Mixed text is counted by
    script, which is what the same sentence carrying a unit or a device name
    needs.
    """
    cjk = len(_CJK.findall(text))
    return cjk / CHARS_PER_SECOND + (len(text) - cjk) / LATIN_CHARS_PER_SECOND


def deliverable(sentence: str, limit: float) -> list[str]:
    """Split a sentence into pieces no longer than `limit` seconds of audio.

    A sentence within the limit is returned untouched, which is the normal
    case — 97% of production sentences. What is left is the run-on that no
    boundary detector can break up: an enumeration, a list read out, a reply
    written without a full stop in it.
    """
    if audio_seconds(sentence) <= limit:
        return [sentence]

    pieces: list[str] = []
    buffer = ""
    for part in _BREAKS.split(sentence):
        if buffer and audio_seconds(buffer + part) > limit:
            pieces.append(buffer)
            buffer = part
        else:
            buffer += part
    if buffer:
        pieces.append(buffer)

    wrapped: list[str] = []
    for piece in pieces:
        wrapped.extend(_wrap(piece, limit) if audio_seconds(piece) > limit else [piece])
    return wrapped


def _wrap(piece: str, limit: float) -> list[str]:
    """Cut a clause that is over the limit on its own.

    Nothing in the production corpus reaches this, so it exists to bound the
    damage rather than to produce a good reading: a caller that sees more than
    one piece come back from here has met text this module has never seen.
    """
    out: list[str] = []
    while audio_seconds(piece) > limit:
        # The characters the limit buys, at the rate this piece reads at.
        room = max(1, int(len(piece) * limit / audio_seconds(piece)))
        cut = piece.rfind(_WORD, 1, room + 1)
        if cut <= 0:
            cut = room
        out.append(piece[:cut].rstrip())
        piece = piece[cut:].lstrip()
    if piece:
        out.append(piece)
    return out
