"""Support for Lutron QS lights."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from pylutron_integration.devices import DeviceUpdate
from pylutron_integration.types import SerialNumber, DeviceAction

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .entity import LutronQSEntity

_LOGGER = logging.getLogger(__name__)

# For now, hardcode 8 zones for GrafikEyeQS
GRAFIK_EYE_ZONE_COUNT = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS lights from a config entry."""

    def create_light_entities(device_sn: SerialNumber) -> list[LutronQSLight]:
        """Create light entities for a device.

        Args:
            device_sn: Serial number of the device

        Returns:
            List of light entities for this device (may be empty)
        """
        universe = entry.runtime_data.universe
        device_details = universe.devices_by_sn.get(device_sn)
        if not device_details:
            return []

        entities: list[LutronQSLight] = []

        # Only handle GrafikEyeQS for now
        if device_details.family == b"GRAFIK_EYE(2)":
            # Get the ZONE component group
            from pylutron_integration.devices import FAMILY_TO_CLASS

            device_class = FAMILY_TO_CLASS.get(device_details.family)
            if not device_class:
                return []

            zone_group = device_class.groups.get("ZONE")
            if not zone_group:
                return []

            # Create a light entity for each zone (hardcoded to 8 for now)
            for zone_index in range(1, GRAFIK_EYE_ZONE_COUNT + 1):
                component_number = zone_group.component_number(zone_index)
                if component_number is None:
                    continue

                entity = LutronQSLight(
                    entry,
                    device_sn,
                    component_number,
                    zone_index,
                    device_details.integration_id.decode()
                    if device_details.integration_id != b"(Not Set)"
                    else str(device_sn),
                )
                entities.append(entity)

        # Actually add the entities using the callback
        if entities:
            async_add_entities(entities, True)

        return entities

    # Register the factory function - process_device_updates will call this for each device
    entry.runtime_data.entity_factories[Platform.LIGHT] = create_light_entities


class LutronQSLight(LutronQSEntity, LightEntity):
    """Representation of a Lutron QS light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        zone_index: int,
        device_name: str,
    ) -> None:
        """Initialize a Lutron QS light."""
        super().__init__(entry, device_sn, component_number)
        self._zone_index = zone_index
        self._device_name = device_name

        # Entity name: "Zone N"
        self._attr_name = f"Zone {zone_index}"

        # State
        self._attr_is_on = False
        self._attr_brightness = 255

    def get_routing_actions(
        self,
    ) -> list[tuple[DeviceAction, Callable[[DeviceUpdate], None]]]:
        """Return the list of (action, handler) tuples for this entity."""
        return [(DeviceAction.LIGHT_LEVEL, self._handle_light_level)]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)

        # Convert brightness (0-255) to Lutron level (0-100)
        level = ((brightness / 255.0) * 100.0)

        try:
            await self._entry.runtime_data.connection.send_device_command(self._device_sn, self._component_number,
                                                                           DeviceAction.LIGHT_LEVEL, [b'%.02f' % level])
        except Exception:
            _LOGGER.exception("Failed to turn on light %s", self.entity_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        try:
            await self._entry.runtime_data.connection.send_device_command(
                self._device_sn,
                self._component_number,
                DeviceAction.LIGHT_LEVEL,
                [b'0'],
            )
        except Exception:
            _LOGGER.exception("Failed to turn off light %s", self.entity_id)

    def _handle_light_level(self, update: DeviceUpdate) -> None:
        """Handle a LIGHT_LEVEL update for this light."""
        if not update.value:
            _LOGGER.warning("Received empty value for light %s", self.entity_id)
            return

        try:
            # Value is a tuple of bytes, first element is the level (0.00-100.00)
            level = float(update.value[0])

            # Convert Lutron level (0-100) to brightness (0-255)
            if level < 0.001:
                self._attr_is_on = False
                self._attr_brightness = 0
            else:
                self._attr_is_on = True
                self._attr_brightness = int((level / 100.0) * 255.0)

            self.async_write_ha_state()

            _LOGGER.debug(
                "Updated light %s: level=%.02f, brightness=%d, is_on=%s",
                self.entity_id,
                level,
                self._attr_brightness,
                self._attr_is_on,
            )
        except (ValueError, IndexError):
            _LOGGER.exception(
                "Failed to parse light level for %s: %s", self.entity_id, update.value
            )
