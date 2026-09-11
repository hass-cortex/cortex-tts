"""Constants for the Hojo TTS integration."""

from __future__ import annotations

DOMAIN = "hojo_tts"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_NORMALIZE_TEXT = "normalize_text"
CONF_CONVERT_SCRIPT = "convert_script"
CONF_STREAM_MODELS = "stream_models"

# Compared against the catalog's `rtf_hint`, which is a relative cost figure
# and not a promise: it separates the two ends of the catalog rather than
# predicting any one host. Sets the default only; an explicit choice wins.
STREAM_RTF_CEILING = 0.5

# Sensor keys a mid-stream push may write. These two are settled the moment the
# first frame leaves — everything else is a total that is not true until the
# last sentence is rendered, and reporting it early would be a lie.
FIRST_AUDIO_FIELDS = frozenset({"first_audio_ms", "streamed"})

# The addon fires this on the HA event bus when its downloaded-model set or its
# voice list changes, so entities appear and disappear without a reload.
EVENT_MODELS_CHANGED = "hojo_tts_models_changed"


def models_changed_signal(entry_id: str) -> str:
    """Return the dispatcher signal fired when the model set changes."""
    return f"{DOMAIN}_models_changed_{entry_id}"
