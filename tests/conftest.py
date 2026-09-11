"""Test fixtures for the Hojo TTS integration.

Home Assistant is not a dependency of this repo: it ships inside Home
Assistant, and installing it here to import three enums would tie the test run
to a Core version this integration does not pin. The module hierarchy below
stands in for exactly the surface `custom_components/hojo_tts` imports —
nothing more, so a new import fails loudly here rather than silently passing
against a mock that answers everything.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import StrEnum
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock


def _module(name: str, **attrs: Any) -> ModuleType:
    """Register a stand-in module and return it."""
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class _HomeAssistantError(Exception):
    """Records the translation metadata the integration attaches to errors."""

    def __init__(
        self,
        message: str = "",
        *,
        translation_domain: str | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


@dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    """The description fields this integration sets. Upstream has many more."""

    key: str
    translation_key: str | None = None
    device_class: Any = None
    native_unit_of_measurement: str | None = None
    state_class: Any = None
    entity_category: Any = None
    suggested_display_precision: int | None = None
    options: list[str] | None = None


class _Entity:
    """Minimal Entity: `_attr_*` assignment and a no-op state write."""

    hass: Any = None

    def async_write_ha_state(self) -> None:
        """Record that a state write happened."""


_module("homeassistant")
_module(
    "homeassistant.core",
    HomeAssistant=MagicMock,
    Event=type("Event", (), {}),
    callback=lambda f: f,
)
_module(
    "homeassistant.exceptions",
    HomeAssistantError=_HomeAssistantError,
    ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (_HomeAssistantError,), {}),
    ConfigEntryNotReady=type("ConfigEntryNotReady", (_HomeAssistantError,), {}),
)


class _EntityCategory(StrEnum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


class _UnitOfTime(StrEnum):
    MILLISECONDS = "ms"
    SECONDS = "s"


_module("homeassistant.const", EntityCategory=_EntityCategory, UnitOfTime=_UnitOfTime)
_module(
    "homeassistant.config_entries",
    ConfigEntry=MagicMock,
    ConfigFlow=type(
        "ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}
    ),
    ConfigFlowResult=dict,
    OptionsFlow=type("OptionsFlow", (), {}),
    OptionsFlowWithReload=type("OptionsFlowWithReload", (), {}),
    ConfigEntryNotReady=sys.modules["homeassistant.exceptions"].ConfigEntryNotReady,
)

_module("homeassistant.helpers")
_module(
    "homeassistant.helpers.config_validation",
    config_entry_only_config_schema=lambda domain: {},
)
_module("homeassistant.helpers.typing", ConfigType=dict, StateType=object)
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=MagicMock())
_module("homeassistant.helpers.entity", Entity=_Entity)
_module("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=object)
_module(
    "homeassistant.helpers.dispatcher",
    async_dispatcher_connect=MagicMock(),
    async_dispatcher_send=MagicMock(),
)


class _DeviceEntryType(StrEnum):
    SERVICE = "service"


_module(
    "homeassistant.helpers.device_registry",
    DeviceEntryType=_DeviceEntryType,
    DeviceInfo=dict,
    async_get=MagicMock(),
)
_module(
    "homeassistant.helpers.selector",
    SelectOptionDict=dict,
    SelectSelector=MagicMock,
    SelectSelectorConfig=MagicMock,
    SelectSelectorMode=MagicMock(),
)
_module("homeassistant.helpers.service_info")


@dataclass
class _HassioServiceInfo:
    config: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    slug: str = ""
    uuid: str = ""


_module(
    "homeassistant.helpers.service_info.hassio", HassioServiceInfo=_HassioServiceInfo
)

_module("homeassistant.components")
_module(
    "homeassistant.components.diagnostics", async_redact_data=lambda data, keys: data
)


class _SensorDeviceClass(StrEnum):
    DURATION = "duration"
    ENUM = "enum"


class _SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL_INCREASING = "total_increasing"


_module(
    "homeassistant.components.sensor",
    SensorDeviceClass=_SensorDeviceClass,
    SensorStateClass=_SensorStateClass,
    SensorEntity=type("SensorEntity", (_Entity,), {}),
    SensorEntityDescription=_SensorEntityDescription,
)


@dataclass
class _TTSAudioRequest:
    language: str
    options: dict[str, Any]
    message_gen: Any


@dataclass
class _TTSAudioResponse:
    extension: str
    data_gen: Any


_module(
    "homeassistant.components.tts",
    ATTR_AUDIO_OUTPUT="audio_output",
    ATTR_PREFERRED_FORMAT="preferred_format",
    ATTR_VOICE="voice",
    TextToSpeechEntity=type("TextToSpeechEntity", (_Entity,), {}),
    TTSAudioRequest=_TTSAudioRequest,
    TTSAudioResponse=_TTSAudioResponse,
    TtsAudioType=tuple,
    Voice=dataclass(
        type("Voice", (), {"__annotations__": {"voice_id": str, "name": str}})
    ),
)
