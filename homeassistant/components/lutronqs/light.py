"""Support for Lutron QS lights."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pylutron_integration.devices import Action, SerialNumber

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from . import DeviceUpdate

_LOGGER = logging.getLogger(__name__)

# For now, hardcode 8 zones for GrafikEyeQS
GRAFIK_EYE_ZONE_COUNT = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS lights from a config entry."""
    universe = entry.runtime_data.universe
    entities: list[LutronQSLight] = []

    # Iterate over all devices and create lights for supported device types
    for device_sn, device_details in universe.devices_by_sn.items():
        # Only handle GrafikEyeQS for now
        if device_details.family == b"GRAFIK_EYE(2)":
            # Get the ZONE component group
            from pylutron_integration.devices import FAMILY_TO_CLASS

            device_class = FAMILY_TO_CLASS.get(device_details.family)
            if not device_class:
                continue

            zone_group = device_class.groups.get("ZONE")
            if not zone_group:
                continue

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

    async_add_entities(entities, True)


class LutronQSLight(LightEntity):
    """Representation of a Lutron QS light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        zone_index: int,
        device_name: str,
    ) -> None:
        """Initialize a Lutron QS light."""
        self._entry = entry
        self._device_sn = device_sn
        self._component_number = component_number
        self._zone_index = zone_index
        self._device_name = device_name

        serial = device_sn.sn.decode()

        # Unique ID: serial_number_component_number
        self._attr_unique_id = f"{serial}_{component_number}"

        # Entity name: "Zone N"
        self._attr_name = f"Zone {zone_index}"

        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
        )

        # State
        self._attr_is_on = False
        self._attr_brightness = 255

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Register this entity in the routing table for LIGHT_LEVEL updates
        routing_key = (self._device_sn, self._component_number, Action.LIGHT_LEVEL.value)
        self._entry.runtime_data.entity_routing_table[routing_key] = self

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is being removed from hass."""
        # Unregister from routing table
        routing_key = (self._device_sn, self._component_number, Action.LIGHT_LEVEL.value)
        self._entry.runtime_data.entity_routing_table.pop(routing_key, None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)

        # Convert brightness (0-255) to Lutron level (0-100)
        level = ((brightness / 255.0) * 100.0)

        # Send LIGHT_LEVEL command to device
        # Format: #DEVICE,serial,component,14,level
        command = (
            f"#DEVICE,{self._device_sn.sn.decode()},"
            f"{self._component_number},{Action.LIGHT_LEVEL.value},{level:.02f}"
        ).encode()

        _LOGGER.info("Turning on light: %r", command)

        try:
            await self._entry.runtime_data.connection.raw_query(command)
        except Exception:
            _LOGGER.exception("Failed to turn on light %s", self.entity_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        # Send LIGHT_LEVEL command with level 0
        command = (
            f"#DEVICE,{self._device_sn.sn.decode()},"
            f"{self._component_number},{Action.LIGHT_LEVEL.value},0"
        ).encode("utf-8")

        _LOGGER.info("Turning off light: %s", command)

        try:
            await self._entry.runtime_data.connection.raw_query(command)
        except Exception:
            _LOGGER.exception("Failed to turn off light %s", self.entity_id)

    def handle_update(self, update: DeviceUpdate) -> None:
        """Handle a device update for this light.

        Called when a ~DEVICE message is received with matching serial/component/action.
        """
        # We only care about LIGHT_LEVEL actions for lights
        if update.action != Action.LIGHT_LEVEL.value:
            _LOGGER.debug(
                "Light %s ignoring non-LIGHT_LEVEL action: %d",
                self.entity_id,
                update.action,
            )
            return

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
