"""Diagnostic sensors: what the last synthesis cost and how it was delivered.

Real-time factor is the number that matters on a host without a GPU — it says
whether the box can still speak faster than it plays, which is also what
decides whether streaming is worth using. These are push sensors fed by the
TTS entity, so they cost nothing when nothing is speaking.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN, SPOKEN_MODES
from .entity import device_for
from .entity_setup import async_setup_dynamic_models
from .models import CortexTTSRuntimeData, ModelInfo, SpeechStats

if TYPE_CHECKING:
    from . import CortexTTSConfigEntry


PARALLEL_UPDATES = 0


def _measured(value: float, digits: int) -> float | None:
    """Return a rounded measurement, or `None` when there was not one.

    Every quantity here is a duration or a ratio of one, so zero means the
    field was never filled in — a synthesis that produced audio cannot have
    taken no time. Reporting it as a number makes a gap in the plumbing look
    like a very fast model.
    """
    return round(value, digits) if value > 0 else None


@dataclass(frozen=True, kw_only=True)
class CortexSensorDescription(SensorEntityDescription):
    """One diagnostic sensor and how to read it from a synthesis."""

    value_fn: Callable[[SpeechStats], StateType]
    """Reads this sensor's value out of one synthesis.

    Returning `None` leaves the sensor unknown, which is how a measurement
    that was not taken is told apart from one that came out at zero. The two
    look identical otherwise, and a real synthesis never costs zero.
    """
    attributes_fn: Callable[[SpeechStats], dict[str, Any]] | None = None
    """Extra attributes read out of the same synthesis, for what is too long
    to be a state — a state is capped at 255 characters."""


# Four describe the last thing said and sit on the device's main card: what
# was said, how it was delivered, how long the listener waited, and whether
# it ran dry. The rest measure the model and are diagnostic.
DESCRIPTIONS: tuple[CortexSensorDescription, ...] = (
    CortexSensorDescription(
        key="inference_ms",
        translation_key="inference_ms",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda stats: _measured(stats.inference_ms, 1),
    ),
    CortexSensorDescription(
        key="first_audio_ms",
        translation_key="first_audio_ms",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda stats: _measured(stats.first_audio_ms, 1),
        # Where the wait went: loading a model that had been unloaded, the
        # writer (a paced reply cannot render before it has finished), and
        # the render itself. Together they explain the total.
        attributes_fn=lambda stats: {
            "load_ms": round(stats.load_ms),
            "writer_ms": None if stats.writer_ms is None else round(stats.writer_ms),
            "render_ms": round(stats.inference_ms),
        },
    ),
    CortexSensorDescription(
        key="rtf",
        translation_key="rtf",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda stats: _measured(stats.rtf, 3),
    ),
    CortexSensorDescription(
        key="audio_seconds",
        translation_key="audio_seconds",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda stats: _measured(stats.audio_seconds, 2),
    ),
    CortexSensorDescription(
        key="characters",
        translation_key="characters",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda stats: stats.characters,
    ),
    # The reply itself. A state is capped at 255 characters, so the state is
    # the opening and the attribute is the whole.
    CortexSensorDescription(
        key="text",
        translation_key="text",
        value_fn=lambda stats: stats.text[:255] or None,
        attributes_fn=lambda stats: {"text": stats.text},
    ),
    # "Did it play smoothly", as far as the app can answer it: a negative
    # margin is audio that did not exist yet, which nothing downstream can
    # rescue.
    CortexSensorDescription(
        key="margin_seconds",
        translation_key="margin_seconds",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda stats: (
            None if stats.margin_seconds is None else round(stats.margin_seconds, 2)
        ),
    ),
    # How many requests the reply was rendered in. The key predates the
    # name: changing it would rename every installed entity.
    CortexSensorDescription(
        key="requests",
        translation_key="requests",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda stats: stats.batches or None,
    ),
    # How the reply was actually spoken, as the app reported it — not the
    # setting, which only says whether the app was allowed to choose.
    CortexSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(SPOKEN_MODES),
        value_fn=lambda stats: stats.mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: CortexTTSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for each usable model."""

    def build(model: ModelInfo) -> list[CortexTTSSensor]:
        sensors = [
            CortexTTSSensor(config_entry, model, description)
            for description in DESCRIPTIONS
        ]
        runtime: CortexTTSRuntimeData = config_entry.runtime_data
        runtime.sensors_by_model[model.id] = list(sensors)
        return sensors

    async_setup_dynamic_models(hass, config_entry, async_add_entities, build)


class CortexTTSSensor(SensorEntity):
    """One fact about the most recent synthesis."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description: CortexSensorDescription

    def __init__(
        self,
        config_entry: CortexTTSConfigEntry,
        model: ModelInfo,
        description: CortexSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._model = model
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{model.id}_{description.key}"
        )
        self._attr_device_info = device_for(config_entry.entry_id, model)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    @callback
    def handle_speech_start(self) -> None:
        """Clear the previous reply's number as a new one begins.

        A reply publishes its facts at two moments, so a value left standing
        from the reply before would sit beside a fresh one and read as though
        both described the same utterance.
        """
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        if self.hass is not None:
            self.async_write_ha_state()

    @callback
    def handle_speech(
        self, stats: SpeechStats, fields: frozenset[str] | None = None
    ) -> None:
        """Record one synthesis, or the part of it that is known so far.

        A streamed reply learns its facts at two different moments: how long
        the listener waited is settled at the first frame, while the totals
        are only true once the last sentence is rendered. ``fields`` names
        which sensors a push is allowed to touch, so each number lands as
        soon as it becomes true instead of at the end of the reply.

        A failed synthesis writes nothing: the sensors were cleared when the
        reply began, so they read unknown rather than zero, and zero would
        read as "instant" — the opposite of what happened.
        """
        if not stats.success:
            return
        if fields is not None and self.entity_description.key not in fields:
            return

        self._attr_native_value = self.entity_description.value_fn(stats)
        if self.entity_description.attributes_fn is not None:
            self._attr_extra_state_attributes = self.entity_description.attributes_fn(
                stats
            )

        if self.hass is not None:
            self.async_write_ha_state()
