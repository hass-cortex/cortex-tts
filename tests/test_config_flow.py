"""Supervisor discovery, and the restart loop it used to cause.

The Supervisor replays its discovery records to Home Assistant on every start.
`async_step_hassio` answers a replay by adopting the matching entry, and
`async_update_reload_and_abort` reloads unconditionally unless told otherwise —
so every restart tore the entry down and set it up again. Setup asks the app
for every model's voice list, which is not free, so the second pass was
visible as a second round of model loads in the app log.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.cortex_tts.config_flow import (
    CortexTTSConfigFlow,
    normalise_host,
)
from custom_components.cortex_tts.const import CONF_API_KEY, CONF_HOST

HOST = "http://local-cortex-tts:8771"
UUID = "0a1b2c3d"


class _Entry:
    def __init__(self, **data: Any) -> None:
        self.data = data


class _Flow(CortexTTSConfigFlow):
    """The real flow, with the two Core methods it calls recorded."""

    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries
        self.aborted: dict[str, Any] | None = None

    def _async_current_entries(self, include_ignore: bool = True) -> list[_Entry]:
        del include_ignore
        return self._entries

    def async_update_reload_and_abort(
        self, entry: Any, **kwargs: Any
    ) -> dict[str, Any]:
        self.aborted = {"entry": entry, **kwargs}
        return {"type": "abort"}


def _discovery(api_key: str = "key-1") -> HassioServiceInfo:
    return HassioServiceInfo(
        config={"host": "local-cortex-tts", "port": 8771, "api_key": api_key},
        name="Cortex TTS",
        slug="cortex_tts",
        uuid=UUID,
    )


async def test_a_replayed_discovery_does_not_reload_an_unchanged_entry() -> None:
    """The fix: nothing changed, so nothing is torn down."""
    entry = _Entry(**{CONF_HOST: HOST, CONF_API_KEY: "key-1"})
    flow = _Flow([entry])

    await flow.async_step_hassio(_discovery())

    assert flow.aborted is not None
    assert flow.aborted["reload_even_if_entry_is_unchanged"] is False


async def test_the_entry_is_still_updated_and_adopted() -> None:
    """A rotated key must reach the entry — that is what this branch is for.

    Core reloads when `data_updates` or `unique_id` actually change, so opting
    out of the unconditional reload does not opt out of this one.
    """
    entry = _Entry(**{CONF_HOST: HOST, CONF_API_KEY: "old-key"})
    flow = _Flow([entry])

    await flow.async_step_hassio(_discovery(api_key="rotated"))

    assert flow.aborted is not None
    assert flow.aborted["entry"] is entry
    assert flow.aborted["data_updates"] == {CONF_HOST: HOST, CONF_API_KEY: "rotated"}
    assert flow.aborted["unique_id"] == UUID


class _AbortError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _UserFlow(_Flow):
    """The manual flow, with Core's duplicate checks standing in."""

    def __init__(self, entries: list[_Entry]) -> None:
        super().__init__(entries)
        self.hass = MagicMock()
        self.unique_id: str | None = None

    def _async_abort_entries_match(self, match: dict[str, Any]) -> None:
        for entry in self._entries:
            if all(entry.data.get(k) == v for k, v in match.items()):
                raise _AbortError("already_configured")

    async def _validate_input(self, host: str, api_key: str) -> tuple[None, Any]:
        return None, MagicMock()

    async def _titled(self, client: Any, base: str) -> str:
        return base

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        return None

    def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict:
        return {"type": "create_entry", "title": title, "data": data}


class TestOneEntryPerServer:
    """A discovered entry and a hand-added one must not both point at a host."""

    @pytest.mark.parametrize("typed", [HOST, HOST + "/", "local-cortex-tts:8771"])
    async def test_adding_a_discovered_server_by_hand_is_refused(
        self, typed: str
    ) -> None:
        discovered = _Entry(**{CONF_HOST: HOST, CONF_API_KEY: "key-1"})
        flow = _UserFlow([discovered])
        with pytest.raises(_AbortError, match="already_configured"):
            await flow.async_step_user({CONF_HOST: typed, CONF_API_KEY: "key-1"})

    async def test_the_stored_host_is_normalised(self) -> None:
        flow = _UserFlow([])
        result = await flow.async_step_user(
            {CONF_HOST: "homeassistant.local:8771/", CONF_API_KEY: "k"}
        )
        assert result["data"][CONF_HOST] == "http://homeassistant.local:8771"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://x:8771", "http://x:8771"),
        ("http://x:8771/", "http://x:8771"),
        (" x:8771 ", "http://x:8771"),
        ("https://x", "https://x"),
    ],
)
def test_normalise_host(raw: str, expected: str) -> None:
    assert normalise_host(raw) == expected
