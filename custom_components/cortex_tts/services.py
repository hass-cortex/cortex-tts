"""Services this integration adds.

`list_voices` exists because Home Assistant publishes an engine's voice list
only over the WebSocket command `tts/engine/voices`, so nothing in YAML can
reach it — and the ids differ per model, so they cannot be guessed.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
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

ATTR_ENTITY = "entity_id"

LIST_VOICES_SCHEMA = vol.Schema(
    {
        # A TTS entity, not any entity: only that platform's unique id is
        # `<domain>_<entry>_<model>`. A sensor's carries a trailing key, which
        # would be read as part of the model id.
        vol.Optional(ATTR_ENTITY): cv.entity_domain(Platform.TTS),
    }
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


def _target_of_entity(hass: HomeAssistant, entity_id: str) -> tuple[str, str] | None:
    """Return the (config entry, model) an entity speaks with, or None.

    Both halves, because a model id names a model on one server: two servers
    can each offer `moss-nano`, and their cloned voices are not the same set.
    """
    record = er.async_get(hass).async_get(entity_id)
    if record is None or record.config_entry_id is None:
        return None
    model = model_from_unique_id(record.config_entry_id, record.unique_id)
    return None if model is None else (record.config_entry_id, model)


async def _list_voices(call: ServiceCall) -> ServiceResponse:
    """Return every voice, or those one entity speaks with.

    Raises:
        ServiceValidationError: The entity is not a Cortex TTS one, its server
            is not loaded, or that server has dropped the model — answering
            with an empty list would read as "this model has no voices", which
            is a different problem.
    """
    hass = call.hass
    entries = _entries(hass)
    wanted: str | None = None
    entity_id: str | None = call.data.get(ATTR_ENTITY)

    if entity_id:
        target = _target_of_entity(hass, entity_id)
        if target is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        entry_id, wanted = target
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entity_id": entity_id},
            )

    voices: list[dict[str, object]] = []
    known: set[str] = set()
    for entry in entries:
        runtime = entry.runtime_data
        for model in runtime.models:
            known.add(model.id)
            if wanted and model.id != wanted:
                continue
            voices.extend(
                _voice_out(voice, model) for voice in runtime.voices.get(model.id, [])
            )

    if entity_id and wanted and wanted not in known:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_model",
            translation_placeholders={
                "entity_id": entity_id,
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
