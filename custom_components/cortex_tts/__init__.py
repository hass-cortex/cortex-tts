"""Cortex TTS integration for Home Assistant."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Final

import aiohttp
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryNotReady,
    ConfigSubentry,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .client import CortexTTSClient
from .const import (
    CONF_API_KEY,
    CONF_HOST,
    DOMAIN,
    EVENT_MODELS_CHANGED,
    SUBENTRY_TYPE,
    models_changed_signal,
)
from .models import CortexTTSRuntimeData, ModelInfo, VoiceInfo

type CortexTTSConfigEntry = ConfigEntry[CortexTTSRuntimeData]

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
    client: CortexTTSClient, models: list[ModelInfo]
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


async def async_setup_entry(hass: HomeAssistant, entry: CortexTTSConfigEntry) -> bool:
    """Set up Cortex TTS from a config entry."""
    session = async_get_clientsession(hass)
    client = CortexTTSClient(
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

    entry.runtime_data = CortexTTSRuntimeData(
        client=client, models=models, voices=voices
    )
    _drop_retired_options(hass, entry)
    _sync_model_subentries(hass, entry, models)
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
        _sync_model_subentries(hass, entry, refreshed)
        _remove_stale_devices(hass, entry, {model.id for model in refreshed})
        async_dispatcher_send(hass, models_changed_signal(entry.entry_id), refreshed)

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_MODELS_CHANGED, _handle_models_changed)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CortexTTSConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# Options that moved into per-model subentries. Left in place they are dead
# weight in diagnostics, read as configuration by anyone looking, and would
# quietly come back to life if a key were ever reused.
RETIRED_OPTIONS: Final = ("stream_models",)


@callback
def _drop_retired_options(hass: HomeAssistant, entry: CortexTTSConfigEntry) -> None:
    """Forget options nothing reads any more."""
    stale = [key for key in RETIRED_OPTIONS if key in entry.options]
    if not stale:
        return
    options = {k: v for k, v in entry.options.items() if k not in stale}
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info("dropped options that moved into per-model settings: %s", stale)


@callback
def _sync_model_subentries(
    hass: HomeAssistant, entry: CortexTTSConfigEntry, models: list[ModelInfo]
) -> None:
    """Give every model a subentry to hold its options, and only every model.

    Which models exist is the server's business — they appear when downloaded
    and vanish when deleted — so the subentries are reconciled against that
    list rather than created by the user. A model that goes away takes its
    options with it; leaving them behind would mean a redownload silently
    inherits settings from a configuration nobody remembers making.

    Options themselves are read live from the subentry at synthesis time, so
    this does not reload the entry and neither does editing one.
    """
    wanted = {model.id: model for model in models}
    existing = {
        subentry.unique_id: subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE
    }

    for model_id, model in wanted.items():
        current = existing.get(model_id)
        if current is None:
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType({}),
                    subentry_type=SUBENTRY_TYPE,
                    title=model.name,
                    unique_id=model_id,
                ),
            )
        elif current.title != model.name:
            hass.config_entries.async_update_subentry(entry, current, title=model.name)

    for model_id, subentry in existing.items():
        if model_id not in wanted:
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)


@callback
def _remove_stale_devices(
    hass: HomeAssistant, entry: CortexTTSConfigEntry, current_ids: set[str]
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
