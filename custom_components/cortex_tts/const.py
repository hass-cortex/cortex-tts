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
# decides per reply from what it has measured, or a person names the trade they
# want and it stops deciding. The app publishes no figure to choose from — it
# measures its own host — so what is picked here is the trade, not a number.
STREAM_AUTO = "auto"
"""Let the app pace the reply, streamed or planned, from what it measured."""
STREAM_PLANNED = "planned"
"""Wait for the words, then cut so playback never catches the renderer. Never
stalls; on a model slower than its own audio the first word can be minutes."""
STREAM_UNHELD = "unheld"
"""The same words cut for the soonest first word, released with no hold at
all. Stalls on a model that cannot keep ahead — that is the trade."""
STREAM_BUFFERED = "buffered"
"""Render the whole reply, then play it. No stall, the longest wait, and the
only way to get a reply the model never had to cut."""
STREAM_MODES = (STREAM_AUTO, STREAM_PLANNED, STREAM_UNHELD, STREAM_BUFFERED)

# The two words the setting used to have for streaming. A stored value from
# then means the person wanted the reply spoken as it was written, which is
# now `auto`; reading it as anything else would silently turn streaming off.
LEGACY_STREAM_MODES = frozenset({"sentence", "coalesced"})

# What the app reports a reply was actually spoken as, in its `done` frame.
# Three of them are settings too — naming one is asking for that outcome — and
# the two here are outcomes only: `streaming` is something `auto` arrives at,
# and `whole` is any plan that turned out to fit a single request.
STREAM_WHOLE = "whole"
STREAM_STREAMING = "streaming"
SPOKEN_MODES = (
    STREAM_WHOLE,
    STREAM_STREAMING,
    STREAM_PLANNED,
    STREAM_UNHELD,
    STREAM_BUFFERED,
)
"""How a reply is being spoken, as the app reports it. Not the setting — that
is `STREAM_MODES`.

Every value either frame can carry, because the sensor is written from both:
a `batch` frame names the plan in force (`streaming`, `planned`, `unheld` or `buffered`)
before any audio exists, and `done` corrects it afterwards with what actually
happened (`whole` when the reply fit one request, however it was planned —
except `buffered`, which is about releasing rather than cutting and so says
so whatever the count).
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
