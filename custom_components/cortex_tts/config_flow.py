"""Config flow for the Cortex TTS integration."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .client import CortexTTSClient
from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_STREAM_MODE,
    DOMAIN,
    STREAM_MODES,
    SUBENTRY_TYPE,
)
from .models import stream_mode_setting

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


# What the app serves on unless told otherwise, and what a title falls back
# to when the address was typed without one.
DEFAULT_PORT = 8771


def normalise_host(host: str) -> str:
    """One spelling per server, so two entries cannot point at the same one."""
    host = host.strip().rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    return host


def server_label(host: str) -> str:
    """What to call a server in an entry title.

    Host and port, because together they are what differs between two entries
    and what does not change. Not the app's version: read once from `/health`
    at setup it would name whatever ran that day, never move again, and say
    nothing about which machine — the one question a title has to answer as
    soon as there is more than one server.

    The port is shown even when it is the default, so the title is the address
    a reader can paste rather than one they have to complete from memory.
    """
    parsed = urlparse(normalise_host(host))
    return f"{parsed.hostname or host}:{parsed.port or DEFAULT_PORT}"


class CortexTTSConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Cortex TTS."""

    VERSION = 1

    _hassio_discovery: HassioServiceInfo | None = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Every downloaded model is a subentry with its own settings.

        There are no entry-level options left: everything configurable here is
        a property of one model, and a setting that applies to all of them was
        only ever a list of per-model answers wearing a single form.
        """
        return {SUBENTRY_TYPE: ModelSubentryFlow}

    async def _validate_input(
        self, host: str, api_key: str
    ) -> tuple[str | None, CortexTTSClient]:
        """Validate host and API key, returning (error_key, client)."""
        session = async_get_clientsession(self.hass)
        client = CortexTTSClient(host=host, api_key=api_key, session=session)
        return await client.validate(), client

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalise_host(user_input[CONF_HOST])
            # A discovered entry carries the Supervisor's uuid, not a hash of
            # the host, so the unique id alone cannot catch this duplicate.
            self._async_abort_entries_match({CONF_HOST: host})
            error, client = await self._validate_input(host, user_input[CONF_API_KEY])
            if error:
                errors["base"] = error
            else:
                unique_id = hashlib.sha256(host.encode()).hexdigest()[:16]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Cortex TTS ({server_label(host)})",
                    data={CONF_HOST: host, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            # hassfest forbids a URL inside a translated string.
            description_placeholders={"example": "http://homeassistant.local:8771"},
        )

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle Supervisor discovery.

        The addon publishes ``{host, port, api_key}``. A fresh payload with a
        rotated key updates the stored credentials in place, so rotating the
        key in the addon's options never means a manual reauth.
        """
        _LOGGER.debug("Supervisor discovery: %s", discovery_info)

        host = f"http://{discovery_info.config['host']}:{discovery_info.config['port']}"
        api_key = discovery_info.config["api_key"]

        # Adopt a matching hand-added entry: its unique id is a hash of the
        # host, not the Supervisor uuid, so the fast path below would miss it.
        # The Supervisor replays discovery on every Home Assistant start, so
        # this must not reload an entry whose data did not change.
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_HOST) == host:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_API_KEY: api_key},
                    unique_id=discovery_info.uuid,
                    reason="already_configured",
                    reload_even_if_entry_is_unchanged=False,
                )

        await self.async_set_unique_id(discovery_info.uuid)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_API_KEY: api_key}
        )

        self._hassio_discovery = discovery_info
        self.context.update(
            {
                "title_placeholders": {"name": discovery_info.name},
                "configuration_url": (
                    f"homeassistant://hassio/addon/{discovery_info.slug}/info"
                ),
            }
        )
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Supervisor discovery and create the entry."""
        assert self._hassio_discovery is not None
        discovery = self._hassio_discovery
        errors: dict[str, str] = {}

        if user_input is not None:
            host = f"http://{discovery.config['host']}:{discovery.config['port']}"
            api_key = discovery.config["api_key"]
            error, client = await self._validate_input(host, api_key)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"{discovery.name} ({server_label(host)})",
                    data={CONF_HOST: host, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"addon": discovery.name},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauth when the stored key stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a replacement API key."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            error, _ = await self._validate_input(
                reauth_entry.data[CONF_HOST], user_input[CONF_API_KEY]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )


class ModelSubentryFlow(ConfigSubentryFlow):
    """One model's own settings.

    A subentry here stands for a model the server has already downloaded, so
    there is nothing to add — `async_step_user` exists only to say so, because
    Home Assistant offers the button whatever the integration intends.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Refuse: a model appears by being downloaded in the app."""
        return self.async_abort(reason="model_comes_from_the_server")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit how this model speaks."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            # The form is the whole of a model's settings, so the data is
            # replaced rather than merged: a key an older release stored does
            # not survive the next save.
            return self.async_update_and_abort(entry, subentry, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STREAM_MODE,
                    default=stream_mode_setting(subentry.data.get(CONF_STREAM_MODE)),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=mode, label=mode)
                            for mode in STREAM_MODES
                        ],
                        translation_key=CONF_STREAM_MODE,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"model": subentry.title},
        )
