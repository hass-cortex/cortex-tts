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

# How a reply that is still being written reaches the speaker. Either the app
# decides per reply from the real-time factor it has measured for this model
# and voice on its own host, or a person insists on one outcome and it stops
# deciding. The app publishes no figure to choose from — it measures its own
# host — so what is picked here is the trade, not a number.
STREAM_AUTO = "auto"
"""Let the app decide per reply: streamed when the measured real-time factor
says the model keeps ahead of its audio, buffered when it does not or when
it has not been measured yet. Never runs dry."""
STREAM_STREAMING = "streaming"
"""Insist on streaming: speak the reply as it is written, whatever the app
measured. On a host that cannot keep ahead of its audio, playback stalls
between requests — that is the trade."""
STREAM_BUFFERED = "buffered"
"""Insist on buffering: render the whole reply, then play it. Never stalls,
the longest wait before the first word."""
STREAM_MODES = (STREAM_AUTO, STREAM_STREAMING, STREAM_BUFFERED)

# Values earlier settings could hold. Each of them meant "let the reply be
# spoken as it is written", which is what `auto` asks for; reading one as
# anything else would silently turn streaming off.
LEGACY_STREAM_MODES = frozenset({"sentence", "coalesced", "planned", "unheld"})

SPOKEN_MODES = (STREAM_STREAMING, STREAM_BUFFERED)
"""How a reply is being spoken, as the app reports it: the two outcomes,
never `auto`, which is a setting that names no outcome.

Every value either frame can carry, because the sensor is written from both:
a `batch` frame names the verdict in force before any audio exists, and
`done` repeats what actually happened. Omitting one is not a wrong label but
a failed reply: an enum sensor handed a state outside its options raises, and
the exception surfaces as a 500 from `/api/tts_proxy`, so nothing plays at
all."""

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
