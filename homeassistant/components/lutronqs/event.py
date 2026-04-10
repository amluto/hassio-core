"""Support for Lutron QS keypad button events."""

from __future__ import annotations

from collections.abc import Callable
import logging

from lutron_integration.devices import DeviceUpdate, FAMILY_TO_CLASS
from lutron_integration.types import SerialNumber, DeviceAction

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .entity import LutronQSEntity

_LOGGER = logging.getLogger(__name__)

# Event types exposed to Home Assistant
EVENT_TYPE_PRESS = "Press"
EVENT_TYPE_RELEASE = "Release"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS keypad button events from a config entry."""

    def create_button_event_entities(
        device_sn: SerialNumber, probe_results: list[DeviceUpdate] | None
    ) -> list[LutronQSEntity]:
        """Create button event entities for a keypad.

        Creates one event entity per button component (all possible buttons).
        This includes ordinary buttons plus the four raise/lower buttons.

        Args:
            device_sn: Serial number of the device
            probe_results: Unused for keypads (cannot probe button state)

        Returns:
            List of button event entities for this device (may be empty)
        """
        universe = entry.runtime_data.universe
        device_details = universe.devices_by_sn.get(device_sn)
        if not device_details:
            return []

        entities: list[LutronQSEntity] = []

        # Only handle KEYPAD(1) family
        if device_details.family == b"KEYPAD(1)":
            device_class = FAMILY_TO_CLASS.get(device_details.family)
            if not device_class:
                return []

            # Get the BUTTON component group
            button_group = device_class.groups.get("BUTTON")
            if not button_group:
                return []

            # Create event entity for each possible button
            for button_index in range(1, button_group.count + 1):
                component_number = button_group.component_number(button_index)
                if component_number is None:
                    continue

                entity = LutronQSButtonEvent(
                    entry,
                    device_sn,
                    component_number,
                    button_index,
                    device_details.integration_id.decode()
                    if device_details.integration_id != b"(Not Set)"
                    else str(device_sn),
                )
                entities.append(entity)

            _LOGGER.debug(
                "Created %d button event entities for keypad %s",
                len(entities),
                device_sn.sn.decode(),
            )

        # Actually add the entities using the callback
        if entities:
            async_add_entities(entities, True)

        return entities

    # Register the factory function - process_device_updates will call this for each device
    entry.runtime_data.entity_factories[Platform.EVENT] = create_button_event_entities


class LutronQSButtonEvent(LutronQSEntity, EventEntity):
    """Representation of a Lutron QS keypad button event."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [EVENT_TYPE_PRESS, EVENT_TYPE_RELEASE]

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        button_index: int,
        device_name: str,
    ) -> None:
        """Initialize a Lutron QS button event entity."""
        super().__init__(entry, device_sn, component_number, unique_id_suffix=f"button_{button_index}")
        self._button_index = button_index
        self._device_name = device_name

        # Entity name: "Button N"
        self._attr_name = f"Button {button_index}"

    def get_routing_actions(
        self,
    ) -> list[tuple[DeviceAction, Callable[[DeviceUpdate], None]]]:
        """Return the list of (action, handler) tuples for this entity."""
        return [
            (DeviceAction.PRESS_CLOSE_UNOCC, self._handle_button_press),
            (DeviceAction.RELEASE_OPEN_OCC, self._handle_button_release),
        ]

    def _handle_button_press(self, update: DeviceUpdate) -> None:
        """Handle a BUTTON_PRESS update for this button."""
        _LOGGER.debug(
            "Button %d pressed on keypad %s (component %d)",
            self._button_index,
            self._device_sn.sn.decode(),
            self._component_number,
        )
        self._trigger_event(EVENT_TYPE_PRESS)
        self.async_write_ha_state()

    def _handle_button_release(self, update: DeviceUpdate) -> None:
        """Handle a BUTTON_RELEASE update for this button."""
        _LOGGER.debug(
            "Button %d released on keypad %s (component %d)",
            self._button_index,
            self._device_sn.sn.decode(),
            self._component_number,
        )
        self._trigger_event(EVENT_TYPE_RELEASE)
        self.async_write_ha_state()
