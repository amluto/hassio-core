"""Support for Lutron QS lights."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from lutron_integration.devices import DeviceUpdate
from lutron_integration.types import SerialNumber, DeviceAction

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .entity import LutronQSEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS lights from a config entry."""

    def create_light_entities(
        device_sn: SerialNumber, probe_results: list[DeviceUpdate] | None
    ) -> list[LutronQSEntity]:
        """Create light entities for a device.

        Only creates ZONE entities for components that appear in probe results.
        This allows discovering the actual number of zones (can be more than 8).

        Args:
            device_sn: Serial number of the device
            probe_results: Probe results from the device, used to determine which zones exist

        Returns:
            List of light entities for this device (may be empty)
        """
        universe = entry.runtime_data.universe
        device_details = universe.devices_by_sn.get(device_sn)
        if not device_details:
            return []

        entities: list[LutronQSEntity] = []

        # Only handle GrafikEyeQS for now
        if device_details.family == b"GRAFIK_EYE(2)":
            # Get the ZONE component group
            from lutron_integration.devices import FAMILY_TO_CLASS

            device_class = FAMILY_TO_CLASS.get(device_details.family)
            if not device_class:
                return []

            zone_group = device_class.groups.get("ZONE")
            if not zone_group:
                return []

            # Determine which zone components exist based on probe results
            # Build a map of component_number -> zone_index for all possible zones
            component_to_zone: dict[int, int] = {}
            for zone_index in range(1, zone_group.count + 1):
                component_number = zone_group.component_number(zone_index)
                if component_number is not None:
                    component_to_zone[component_number] = zone_index

            # Filter probe results to find which zone components actually responded
            zone_components: set[int] = set()
            if probe_results:
                for update in probe_results:
                    if update.component in component_to_zone:
                        zone_components.add(update.component)

            # If no probe results provided, create all possible zones (fallback for compatibility)
            if not probe_results:
                zone_components = set(component_to_zone.keys())

            # Create entities only for zones that exist
            for component_number in sorted(zone_components):
                zone_index = component_to_zone[component_number]
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

            _LOGGER.debug(
                "Created %d zone entities for device %s (out of %d possible zones)",
                len(entities),
                device_sn.sn.decode(),
                zone_group.count,
            )

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
    _attr_supported_features = LightEntityFeature.TRANSITION

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
        transition = kwargs.get(ATTR_TRANSITION)

        # Convert brightness (0-255) to Lutron level (0-100)
        level = (brightness / 255.0) * 100.0

        # Build parameters: level is always first
        params = [b"%.02f" % level]

        # Add transition time if specified (format: SS.ss)
        if transition is not None:
            # Home Assistant transition is in seconds (can be float)
            params.append(b"%.02f" % transition)

        try:
            await self._entry.runtime_data.connection.send_device_command(
                self._device_sn,
                self._component_number,
                DeviceAction.LIGHT_LEVEL,
                params,
            )
        except Exception:
            _LOGGER.exception("Failed to turn on light %s", self.entity_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = kwargs.get(ATTR_TRANSITION)

        # Build parameters: level is always first
        params = [b"0"]

        # Add transition time if specified (format: SS.ss)
        if transition is not None:
            # Home Assistant transition is in seconds (can be float)
            params.append(b"%.02f" % transition)

        try:
            await self._entry.runtime_data.connection.send_device_command(
                self._device_sn,
                self._component_number,
                DeviceAction.LIGHT_LEVEL,
                params,
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
