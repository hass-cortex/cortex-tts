"""Diagnostics for the Hojo TTS integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_STREAM_MODELS, STREAM_RTF_CEILING

if TYPE_CHECKING:
    from . import HojoTTSConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HojoTTSConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The server's own health and model list are included: most reports about
    "no voices" are really "the model was never downloaded", and that is only
    visible from the server side. ``streams`` is reported per model for the
    same reason: whether a reply is spoken sentence by sentence depends on the
    model's speed, not only on the option.
    """
    runtime = entry.runtime_data
    health: dict[str, Any] | str
    try:
        health = await runtime.client.health()
    except (aiohttp.ClientError, TimeoutError) as err:
        health = f"unreachable: {err}"

    chosen = entry.options.get(CONF_STREAM_MODELS)

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "server": health,
        "stream_rtf_ceiling": STREAM_RTF_CEILING,
        "models": [
            {
                "id": model.id,
                "kind": model.kind,
                "languages": model.languages,
                "downloaded": model.downloaded,
                "loaded": model.loaded,
                "rtf_hint": model.rtf_hint,
                "streams": model.outruns_playback
                if chosen is None
                else model.id in chosen,
                "voices": [
                    {"id": v.id, "language": v.language, "source": v.source}
                    for v in runtime.voices.get(model.id, [])
                ],
            }
            for model in runtime.models
        ],
    }
