"""Shared helper for live, event-driven model entity setup.

Each platform builds entities for the models present at setup, then keeps
listening: when the addon fires a models-changed event, the dispatcher signal
fires and any newly-usable model gets its entities added without a config-entry
reload. Removal is handled centrally by device removal in ``__init__``; this
helper only adds.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import models_changed_signal
from .models import ModelInfo

if TYPE_CHECKING:
    from . import HojoTTSConfigEntry


@callback
def async_setup_dynamic_models(
    hass: HomeAssistant,
    config_entry: HojoTTSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build_entities: Callable[[ModelInfo], Iterable[Entity]],
) -> None:
    """Add entities for current models and any that appear later."""
    known: set[str] = set()

    @callback
    def _sync(models: list[ModelInfo]) -> None:
        # Prune ids whose models are gone so a deleted-then-re-added model is
        # picked up again; then add entities for any not-yet-known model.
        known.intersection_update({model.id for model in models})
        new = [model for model in models if model.id not in known]
        if not new:
            return
        # Build and register first, mark known second: if construction raises,
        # the model is retried on the next event instead of being stranded.
        entities = [entity for model in new for entity in build_entities(model)]
        async_add_entities(entities)
        known.update(model.id for model in new)

    _sync(config_entry.runtime_data.models)
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, models_changed_signal(config_entry.entry_id), _sync
        )
    )
