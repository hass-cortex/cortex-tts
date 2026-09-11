"""Config flow for the Hojo TTS integration."""

from __future__ import annotations

import contextlib
import hashlib
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
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

from .client import HojoTTSClient
from .const import CONF_API_KEY, CONF_HOST, CONF_STREAM_MODELS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class HojoTTSConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Hojo TTS."""

    VERSION = 1

    _hassio_discovery: HassioServiceInfo | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return HojoTTSOptionsFlow()

    async def _validate_input(
        self, host: str, api_key: str
    ) -> tuple[str | None, HojoTTSClient]:
        """Validate host and API key, returning (error_key, client)."""
        session = async_get_clientsession(self.hass)
        client = HojoTTSClient(host=host, api_key=api_key, session=session)
        return await client.validate(), client

    async def _titled(self, client: HojoTTSClient, base: str) -> str:
        """Append the server version to a title when it can be read."""
        with contextlib.suppress(
            aiohttp.ClientError, TimeoutError, KeyError, ValueError
        ):
            health = await client.health()
            if version := health.get("version"):
                return f"{base} ({version})"
        return base

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error, client = await self._validate_input(
                user_input[CONF_HOST], user_input[CONF_API_KEY]
            )
            if error:
                errors["base"] = error
            else:
                unique_id = hashlib.sha256(user_input[CONF_HOST].encode()).hexdigest()[
                    :16
                ]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                title = await self._titled(client, "Hojo TTS")
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
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
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_HOST) == host:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_API_KEY: api_key},
                    unique_id=discovery_info.uuid,
                    reason="already_configured",
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
                title = await self._titled(client, discovery.name)
                return self.async_create_entry(
                    title=title, data={CONF_HOST: host, CONF_API_KEY: api_key}
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


class HojoTTSOptionsFlow(OptionsFlowWithReload):
    """Options flow for Hojo TTS."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which models speak sentence by sentence."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        models = self.config_entry.runtime_data.models
        chosen = self.config_entry.options.get(CONF_STREAM_MODELS)
        if chosen is None:
            chosen = [model.id for model in models if model.outruns_playback]

        schema = vol.Schema(
            {
                vol.Required(CONF_STREAM_MODELS, default=chosen): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=model.id, label=model.name)
                            for model in models
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
