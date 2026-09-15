"""Constants for the Cortex TTS integration."""

from __future__ import annotations

DOMAIN = "cortex_tts"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_NORMALIZE_TEXT = "normalize_text"
CONF_CONVERT_SCRIPT = "convert_script"
CONF_TAIWAN_READINGS = "taiwan_readings"
CONF_EXPAND_NUMBERS = "expand_numbers"

# A per-call option the server accepts only from the model that declares it,
# so the entity offers it only where it means something. Home Assistant
# refuses an option an entity has not declared, and its cache key hashes every
# option — so a line said warmly is stored apart from the same line said
# plainly, rather than replayed from it.
#
# There is no option for the language: Home Assistant already passes one, and
# it means the language of the text, which is exactly what the model needs to
# be told.
CONF_STYLE_INSTRUCTION = "instruct"
CONF_STREAM_MODE = "stream_mode"

# One subentry per downloaded model, so each model's options have somewhere to
# live that the UI already knows how to show. The integration mints them from
# what the server reports — a model is added by downloading it in the app, not
# here — so the subentry flow only ever reconfigures.
SUBENTRY_TYPE = "model"

# How a reply that is still being written reaches the speaker. Two settings:
# the app decides per reply from what it has measured, or nothing plays until
# the whole reply is rendered. The app publishes no figure to choose from — it
# measures its own host — so the choice a person makes here is only whether
# to let it.
STREAM_BUFFERED = "buffered"
"""Render the whole reply, then play it. No stall, the longest wait."""
STREAM_AUTO = "auto"
"""Let the app pace the reply: streamed, paced or buffered, chosen per reply
from what it has measured about the model on its host."""
STREAM_MODES = (STREAM_AUTO, STREAM_BUFFERED)

# The two words the setting used to have for streaming. A stored value from
# then means the person wanted the reply spoken as it was written, which is
# now `auto`; reading it as anything else would silently turn streaming off.
LEGACY_STREAM_MODES = frozenset({"sentence", "coalesced"})

# What the app reports a reply was actually spoken as, in its `done` frame.
# The sensor shows these; the setting above offers the two before them.
STREAM_WHOLE = "whole"
STREAM_STREAMING = "streaming"
STREAM_PACED = "paced"
SPOKEN_MODES = (STREAM_WHOLE, STREAM_STREAMING, STREAM_PACED, STREAM_BUFFERED)
"""How a reply is being spoken, as the app reports it. Not the setting — that
is `STREAM_MODES`.

Every value either frame can carry, because the sensor is written from both:
a `batch` frame names the plan in force (`streaming`, `paced` or `buffered`)
before any audio exists, and `done` corrects it afterwards with what actually
happened (`whole` when the reply fit one request, however it was planned).
Omitting one is not a wrong label but a failed reply: an enum sensor handed a
state outside its options raises, and the exception surfaces as a 500 from
`/api/tts_proxy`, so nothing plays at all."""

# Sensor keys a mid-stream push may write. These two are settled the moment the
# first frame leaves — everything else is a total that is not true until the
# last sentence is rendered, and reporting it early would be a lie.
FIRST_AUDIO_FIELDS = frozenset({"first_audio_ms", "mode"})

# Settled the moment the writer finishes, well before the reply is rendered.
TEXT_FIELDS = frozenset({"text", "characters"})

# The addon fires this on the HA event bus when its downloaded-model set or its
# voice list changes, so entities appear and disappear without a reload.
EVENT_MODELS_CHANGED = "cortex_tts_models_changed"


def models_changed_signal(entry_id: str) -> str:
    """Return the dispatcher signal fired when the model set changes."""
    return f"{DOMAIN}_models_changed_{entry_id}"


# What `/api/speak/live` is asked for. MP3 is a bare sequence of
# self-describing frames — no container, no length field, no index — which is
# the only honest thing to send when the length is not known yet. A WAV stream
# has to declare one, and the maximal value it declared was read by a
# general-purpose player as a six-hour file it then waited to buffer: one
# sentence, a long stall, and the rest of the reply at the end.
STREAM_FORMAT = "mp3"
