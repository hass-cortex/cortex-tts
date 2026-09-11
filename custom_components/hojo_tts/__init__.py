"""Hojo TTS integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Final

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .client import HojoTTSClient
from .const import (
    CONF_API_KEY,
    CONF_HOST,
    DOMAIN,
    EVENT_MODELS_CHANGED,
    models_changed_signal,
)
from .models import HojoTTSRuntimeData, ModelInfo, VoiceInfo

type HojoTTSConfigEntry = ConfigEntry[HojoTTSRuntimeData]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = ["tts", "sensor"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _usable(models: list[ModelInfo]) -> list[ModelInfo]:
    """Return models that can actually speak.

    A model with no bundle on disk cannot synthesise, and creating an entity
    for it would put a permanently-failing voice in the pipeline picker.
    """
    return [model for model in models if model.downloaded]


async def _collect_voices(
    client: HojoTTSClient, models: list[ModelInfo]
) -> dict[str, list[VoiceInfo]]:
    """Fetch the voice list for each usable model.

    A model with no voices — a freshly-downloaded cloning model with no
    reference uploaded yet — is kept: the entity should exist and explain
    itself when used, rather than vanish from the picker.
    """
    voices: dict[str, list[VoiceInfo]] = {}
    for model in models:
        try:
            voices[model.id] = await client.list_voices(model.id)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
            _LOGGER.warning("could not list voices for %s: %s", model.id, err)
            voices[model.id] = []
    return voices


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HojoTTSConfigEntry) -> bool:
    """Set up Hojo TTS from a config entry."""
    session = async_get_clientsession(hass)
    client = HojoTTSClient(
        host=entry.data[CONF_HOST],
        api_key=entry.data[CONF_API_KEY],
        session=session,
    )

    error = await client.validate()
    if error == "invalid_api_key":
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_api_key"
        )
    if error:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": error},
        )

    try:
        all_models = await client.list_models()
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="list_models_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    models = _usable(all_models)
    voices = await _collect_voices(client, models)
    _LOGGER.info("discovered %d usable model(s) of %d", len(models), len(all_models))

    entry.runtime_data = HojoTTSRuntimeData(client=client, models=models, voices=voices)
    _remove_stale_devices(hass, entry, {model.id for model in models})

    # Registered before forwarding platforms so an event that lands during the
    # setup window updates runtime_data rather than being dropped.
    async def _handle_models_changed(event: Event) -> None:
        """Reconcile entities when the server's model or voice set changes."""
        try:
            refreshed = _usable(await client.list_models())
            refreshed_voices = await _collect_voices(client, refreshed)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
            _LOGGER.warning("models-changed event but refresh failed: %s", err)
            return
        entry.runtime_data.models = refreshed
        entry.runtime_data.voices = refreshed_voices
        _remove_stale_devices(hass, entry, {model.id for model in refreshed})
        async_dispatcher_send(hass, models_changed_signal(entry.entry_id), refreshed)

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_MODELS_CHANGED, _handle_models_changed)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HojoTTSConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _remove_stale_devices(
    hass: HomeAssistant, entry: HojoTTSConfigEntry, current_ids: set[str]
) -> None:
    """Remove devices for models that are no longer on the server."""
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        for _, identifier in device.identifiers:
            if identifier.startswith(f"{entry.entry_id}_"):
                model_id = identifier.removeprefix(f"{entry.entry_id}_")
                if model_id not in current_ids:
                    _LOGGER.info("removing stale device for model %s", model_id)
                    device_registry.async_remove_device(device.id)
                break
