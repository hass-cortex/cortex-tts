"""Services this integration adds.

`list_voices` exists because Home Assistant publishes an engine's voice list
only over the WebSocket command `tts/engine/voices`, so nothing in YAML can
reach it — and the ids differ per model, so they cannot be guessed.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .models import (
    CortexTTSRuntimeData,
    ModelInfo,
    VoiceInfo,
    model_from_unique_id,
)

SERVICE_LIST_VOICES = "list_voices"

ATTR_MODEL = "model"
ATTR_ENTITY = "entity_id"

LIST_VOICES_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_MODEL): cv.string,
            vol.Optional(ATTR_ENTITY): cv.entity_id,
        }
    ),
    # Both name a model; two answers to one question would have to pick one
    # silently.
    cv.has_at_most_one_key(ATTR_MODEL, ATTR_ENTITY),
)


def _voice_out(voice: VoiceInfo, model: ModelInfo) -> dict[str, object]:
    """Render one voice as the caller needs it to build a `tts.speak`."""
    return {
        "voice": voice.id,
        "name": voice.name,
        "language": voice.language,
        "gender": voice.gender,
        # `builtin` ships with the model; `reference` is a recording someone
        # uploaded, and is the same voice on every model that can clone.
        "source": voice.source,
        "model": model.id,
        "model_name": model.name,
    }


def _entries(hass: HomeAssistant) -> list[ConfigEntry[CortexTTSRuntimeData]]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _model_of_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the model id an entity speaks with, via the entity registry."""
    record = er.async_get(hass).async_get(entity_id)
    if record is None or record.config_entry_id is None:
        return None
    return model_from_unique_id(record.config_entry_id, record.unique_id)


async def _list_voices(call: ServiceCall) -> ServiceResponse:
    """Return every voice, or those of one model.

    Raises:
        ServiceValidationError: A model or entity was named that this
            installation does not have — answering with an empty list would
            read as "this model has no voices", which is a different problem.
    """
    hass = call.hass
    wanted: str | None = call.data.get(ATTR_MODEL)
    if entity_id := call.data.get(ATTR_ENTITY):
        wanted = _model_of_entity(hass, entity_id)
        if wanted is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entity",
                translation_placeholders={"entity_id": entity_id},
            )

    voices: list[dict[str, object]] = []
    known: set[str] = set()
    for entry in _entries(hass):
        runtime = entry.runtime_data
        for model in runtime.models:
            known.add(model.id)
            if wanted and model.id != wanted:
                continue
            voices.extend(
                _voice_out(voice, model) for voice in runtime.voices.get(model.id, [])
            )

    if wanted and wanted not in known:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_model",
            translation_placeholders={
                "model": wanted,
                "known": ", ".join(sorted(known)) or "none",
            },
        )

    return {"voices": voices, "count": len(voices)}


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain's services, once per Home Assistant run."""
    if hass.services.has_service(DOMAIN, SERVICE_LIST_VOICES):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_VOICES,
        _list_voices,
        schema=LIST_VOICES_SCHEMA,
        # The point of the service is the answer, so it only has a response
        # shape — a caller that forgets `response_variable` is told so rather
        # than getting a silent no-op.
        supports_response=SupportsResponse.ONLY,
    )
