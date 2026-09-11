"""What every entity of this integration shares."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .models import ModelInfo


def device_for(entry_id: str, model: ModelInfo) -> DeviceInfo:
    """Return the device a model's TTS entity and sensors all belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{model.id}")},
        name=model.name,
        manufacturer="hojo-tts",
        model=model.id,
        entry_type=DeviceEntryType.SERVICE,
    )
