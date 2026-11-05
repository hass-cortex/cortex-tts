"""Entry setup and unload against Core 2026.11's contract.

Core 2026.11 reads a `None` return as success and a raised error as failure;
a `False` return no longer means anything good. These tests pin that the
integration signals both outcomes the way Core now expects.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError

from custom_components.cortex_tts import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.cortex_tts.const import CONF_API_KEY, CONF_HOST


class _ConfigEntries:
    """Core's platform forwarding, with the unload outcome chosen per test."""

    def __init__(self, *, platforms_unload: bool = True) -> None:
        self._platforms_unload = platforms_unload
        self.forwarded: list[str] = []
        self.unloaded: list[str] = []

    async def async_forward_entry_setups(self, entry: Any, platforms: Any) -> None:
        self.forwarded.extend(platforms)

    async def async_unload_platforms_or_raise(self, entry: Any, platforms: Any) -> None:
        """Core raises when any platform fails to unload."""
        self.unloaded.extend(platforms)
        if not self._platforms_unload:
            raise ConfigEntryError(translation_key="unload_platforms_failed")

    def async_add_subentry(self, entry: Any, subentry: Any) -> bool:
        return True

    def async_update_entry(self, entry: Any, **kwargs: Any) -> bool:
        return True


def _hass(config_entries: _ConfigEntries) -> MagicMock:
    hass = MagicMock()
    hass.config_entries = config_entries
    return hass


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {CONF_HOST: "http://x:8771", CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {}
    entry.state = ConfigEntryState.LOADED
    return entry


def _client(validate_error: str | None = None) -> MagicMock:
    client = MagicMock()
    client.validate = AsyncMock(return_value=validate_error)
    client.list_models = AsyncMock(return_value=[])
    client.list_voices = AsyncMock(return_value=[])
    return client


class TestSetup:
    async def test_a_reachable_server_sets_up_and_forwards_platforms(self) -> None:
        config_entries = _ConfigEntries()
        with patch(
            "custom_components.cortex_tts.CortexTTSClient", return_value=_client()
        ):
            await async_setup_entry(_hass(config_entries), _entry())

        assert set(config_entries.forwarded) == set(PLATFORMS)

    async def test_a_rejected_key_fails_setup_by_raising(self) -> None:
        config_entries = _ConfigEntries()
        with (
            patch(
                "custom_components.cortex_tts.CortexTTSClient",
                return_value=_client("invalid_api_key"),
            ),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await async_setup_entry(_hass(config_entries), _entry())

        assert config_entries.forwarded == []


class TestUnload:
    async def test_unload_unloads_every_platform(self) -> None:
        config_entries = _ConfigEntries()

        await async_unload_entry(_hass(config_entries), _entry())

        assert set(config_entries.unloaded) == set(PLATFORMS)

    async def test_a_platform_that_will_not_unload_fails_the_unload(self) -> None:
        """Returning instead would read as success under Core 2026.11."""
        with pytest.raises(ConfigEntryError):
            await async_unload_entry(
                _hass(_ConfigEntries(platforms_unload=False)), _entry()
            )
