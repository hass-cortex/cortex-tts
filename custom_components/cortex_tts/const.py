"""Constants for the Cortex TTS integration."""

from __future__ import annotations

DOMAIN = "cortex_tts"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_NORMALIZE_TEXT = "normalize_text"
CONF_CONVERT_SCRIPT = "convert_script"
CONF_STREAM_MODE = "stream_mode"
CONF_HEAD_START = "head_start"

# Seconds of audio to hold back before a stream starts playing. A model that
# renders slower than its audio plays loses ground for the whole reply and
# never wins it back, so the listener has to be given the difference up front:
# at a rate of R, a reply of L seconds needs (R-1) x L. MOSS-TTS-Nano measured
# 1.045x on the development host, where two seconds covers a 44-second reply.
#
# Zero by default. The right value is a property of the host rather than the
# model — the catalog's `rtf_hint` was measured elsewhere and was out by 3x
# here — so this is set by whoever can measure it, not guessed from a number
# that cannot know.
DEFAULT_HEAD_START = 0.0
MAX_HEAD_START = 10.0

# A head start is sized for the worst reply, and most replies are not that.
# Once the whole reply is in hand its length is known, so the bank shrinks to
# what a reply that long can actually lose: a one-line answer waits a fifth of
# a second rather than the two seconds a forty-second bulletin needs.
#
# Speech runs at roughly this many characters a second, per script. Measured
# on the Hojo 40M in production: 108 characters of Chinese as 26.6 s, 309 of
# Latin as 21.1 s. MOSS-TTS-Nano reads Chinese slightly faster (4.4); the
# slower figure is the safe one, because everything sized from it — the bank
# below, the request limit — wants to over-estimate rather than under.
CHARS_PER_SECOND = 4.1
LATIN_CHARS_PER_SECOND = 14.7

# How far behind playback a streamed model is assumed to fall while sizing
# that shrink. MOSS measured 1.045x on the development host; a tenth leaves
# room for a worse moment. Only ever lowers the configured head start.
ASSUMED_DEFICIT = 0.10

# The most audio to ask for in one request. Coalescing removed the cost of
# sending one sentence at a time — a fresh prefill for every sentence — but a
# request that is too long costs more than it saves: the model attends over
# everything it has generated so far, so the bill grows with the square of the
# request rather than in proportion to it.
#
# Measured on MOSS-TTS-Nano, the same 55 s story sent in pieces of different
# sizes, as the fraction by which rendering fell behind playback:
#
#     ~1 s per request (one sentence)   +41%
#     ~9 s                               +4.1%
#     ~19 s                              +5.6%
#     ~28 s                              +6.5%
#     ~55 s (one request)               +17.8%
#
# A valley with a cliff on either side. This sits in the flat part of it.
#
# It is a limit on the request, not on the batching: a single sentence over it
# is split at a clause mark rather than sent whole, because a run-on sentence
# is delivered in one lump and the listener waits out the whole of it.
MAX_REQUEST_SECONDS = 14.0

# One subentry per downloaded model, so each model's options have somewhere to
# live that the UI already knows how to show. The integration mints them from
# what the server reports — a model is added by downloading it in the app, not
# here — so the subentry flow only ever reconfigures.
SUBENTRY_TYPE = "model"

# How a reply reaches the speaker. The same three words name the setting and
# the outcome the sensor reports, because they are the same fact: what the
# listener got.
STREAM_BUFFERED = "buffered"
"""Render the whole reply, then play it. No stall, the longest wait."""
STREAM_SENTENCE = "sentence"
"""One request per finished sentence, played as each arrives."""
STREAM_COALESCED = "coalesced"
"""As above, but sentences that arrive while a request is in flight are sent
together. Measured on MOSS-TTS-Nano: three sentences sent one at a time stalled
0.37s at each boundary and left a player 0.45s short, while the same text in
one request never ran dry and reached first audio just as fast."""
STREAM_MODES = (STREAM_BUFFERED, STREAM_SENTENCE, STREAM_COALESCED)

# Published advice, not a rule: the measured RTF to be under before streaming
# is worth enabling. Nothing branches on it.
STREAM_RTF_CEILING = 0.5

# Sensor keys a mid-stream push may write. These two are settled the moment the
# first frame leaves — everything else is a total that is not true until the
# last sentence is rendered, and reporting it early would be a lie.
FIRST_AUDIO_FIELDS = frozenset({"first_audio_ms", "mode"})

# The addon fires this on the HA event bus when its downloaded-model set or its
# voice list changes, so entities appear and disappear without a reload.
EVENT_MODELS_CHANGED = "cortex_tts_models_changed"


def models_changed_signal(entry_id: str) -> str:
    """Return the dispatcher signal fired when the model set changes."""
    return f"{DOMAIN}_models_changed_{entry_id}"


# What `/api/speak/stream` is asked for. MP3 is a bare sequence of
# self-describing frames — no container, no length field, no index — which is
# the only honest thing to send when the length is not known yet. A WAV stream
# has to declare one, and the maximal value it declared was read by a
# general-purpose player as a six-hour file it then waited to buffer: one
# sentence, a long stall, and the rest of the reply at the end.
STREAM_FORMAT = "mp3"
