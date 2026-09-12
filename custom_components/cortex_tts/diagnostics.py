"""Diagnostics for the Cortex TTS integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, STREAM_RTF_CEILING
from .models import default_stream_mode, stream_mode

if TYPE_CHECKING:
    from . import CortexTTSConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CortexTTSConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The server's own health and model list are included: most reports about
    "no voices" are really "the model was never downloaded", and that is only
    visible from the server side. The speaking mode is reported per model
    alongside the default it would fall back to, because a report of "it did
    not stream" is usually a model left on its default rather than a bug.
    """
    runtime = entry.runtime_data
    health: dict[str, Any] | str
    try:
        health = await runtime.client.health()
    except (aiohttp.ClientError, TimeoutError) as err:
        health = f"unreachable: {err}"

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "subentries": [
            {
                "model": subentry.unique_id,
                "title": subentry.title,
                "data": dict(subentry.data),
            }
            for subentry in entry.subentries.values()
        ],
        "server": health,
        "stream_rtf_ceiling": STREAM_RTF_CEILING,
        "models": [
            {
                "id": model.id,
                "builtin_voices": model.builtin_voices,
                "cloning": model.cloning,
                "chunk_streaming": model.chunk_streaming,
                "languages": model.languages,
                "downloaded": model.downloaded,
                "loaded": model.loaded,
                "rtf_hint": model.rtf_hint,
                "stream_mode": stream_mode(entry, model),
                "stream_mode_default": default_stream_mode(model),
                "voices": [
                    {"id": v.id, "language": v.language, "source": v.source}
                    for v in runtime.voices.get(model.id, [])
                ],
            }
            for model in runtime.models
        ],
    }
