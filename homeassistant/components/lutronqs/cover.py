"""Support for Lutron QS shades."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from lutron_integration.devices import DeviceUpdate
from lutron_integration.types import SerialNumber, DeviceAction

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .entity import LutronQSEntity

_LOGGER = logging.getLogger(__name__)

# Map product types to CoverDeviceClass
PRODUCT_TO_DEVICE_CLASS = {
    b"ROLLER(1)": CoverDeviceClass.SHADE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS shades from a config entry."""

    def create_shade_entities(
        device_sn: SerialNumber, probe_results: list[DeviceUpdate] | None
    ) -> list[LutronQSEntity]:
        """Create shade entities for a device.

        Only creates shade entity if component 0 appears in probe results.
        This avoids creating a nonexistent shade entity for shade power supplies,
        which identify themselves as shades but don't have actual shade components.

        Args:
            device_sn: Serial number of the device
            probe_results: Probe results from the device, used to verify shade component exists

        Returns:
            List of shade entities for this device (may be empty)
        """
        universe = entry.runtime_data.universe
        device_details = universe.devices_by_sn.get(device_sn)
        if not device_details:
            return []

        entities: list[LutronQSEntity] = []

        # Only handle SHADES(3) family
        if device_details.family == b"SHADES(3)":
            # Shade component is always component 0
            component_number = 0

            # Only create shade entity if component 0 appears in probe results
            # This filters out shade power supplies which identify as shades but have no shade component
            component_exists = False
            if probe_results:
                for update in probe_results:
                    if update.component == component_number:
                        component_exists = True
                        break

            if not component_exists:
                _LOGGER.info(
                    "Skipping shade entity for device %s - component 0 not found in probe results (likely a power supply)",
                    device_sn.sn.decode(),
                )
                return []

            # Determine cover device class from product type
            cover_device_class = PRODUCT_TO_DEVICE_CLASS.get(
                device_details.product, CoverDeviceClass.SHADE
            )

            entity = LutronQSShade(
                entry,
                device_sn,
                component_number,
                device_details.integration_id.decode()
                if device_details.integration_id != b"(Not Set)"
                else str(device_sn),
                cover_device_class,
            )
            entities.append(entity)

        # Actually add the entities using the callback
        if entities:
            async_add_entities(entities, True)

        return entities

    # Register the factory function - process_device_updates will call this for each device
    entry.runtime_data.entity_factories[Platform.COVER] = create_shade_entities


class LutronQSShade(LutronQSEntity, CoverEntity):
    """Representation of a Lutron QS shade."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        device_name: str,
        cover_device_class: CoverDeviceClass,
    ) -> None:
        """Initialize a Lutron QS shade."""
        super().__init__(entry, device_sn, component_number)
        self._device_name = device_name

        # Entity name: "Shade" (will be combined with device name)
        self._attr_name = "Shade"

        # Device class from product mapping
        self._attr_device_class = cover_device_class

        # State: position 0-100
        self._attr_current_cover_position: int | None = None
        self._previous_position: int | None = None

        # Motion state tracking (MOTOR_MYSTERY action)
        self._motor_mystery: float = 0.0

    def get_routing_actions(
        self,
    ) -> list[tuple[DeviceAction, Callable[[DeviceUpdate], None]]]:
        """Return the list of (action, handler) tuples for this entity."""
        # Shades use LIGHT_LEVEL (action 14) to report position
        # and MOTOR_MYSTERY (action 21) to report motion state.
        # (Actually, I don't know what MOTOR_MYSTERY reports, but
        #  it's nonzero when moving and 0 when stationary.)
        return [
            (DeviceAction.LIGHT_LEVEL, self._handle_position_update),
            (DeviceAction.MOTOR_MYSTERY, self._handle_motion_update),
        ]

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        if self._attr_current_cover_position is None:
            return None
        return self._attr_current_cover_position == 0

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        # If motor is active and we have two distinct positions, determine direction
        if (
            self._motor_mystery != 0.0
            and self._previous_position is not None
            and self._attr_current_cover_position is not None
            and self._previous_position != self._attr_current_cover_position
        ):
            return self._attr_current_cover_position > self._previous_position
        return False

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        # If motor is active and we have two distinct positions, determine direction
        if (
            self._motor_mystery != 0.0
            and self._previous_position is not None
            and self._attr_current_cover_position is not None
            and self._previous_position != self._attr_current_cover_position
        ):
            return self._attr_current_cover_position < self._previous_position
        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the shade."""
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the shade."""
        await self.async_set_cover_position(position=0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the shade to a specific position (0-100)."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return

        # Convert position (0-100) to Lutron level (0-100)
        # Position 0 = closed, 100 = open
        level = float(position)

        try:
            await self._entry.runtime_data.connection.send_device_command(
                self._device_sn,
                self._component_number,
                DeviceAction.LIGHT_LEVEL,
                [b'%.02f' % level],
            )
        except Exception:
            _LOGGER.exception("Failed to set shade position for %s", self.entity_id)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        try:
            await self._entry.runtime_data.connection.send_device_command(
                self._device_sn,
                self._component_number,
                DeviceAction.STOP_RAISING_LOWERING,
                []
            )
        except Exception:
            _LOGGER.exception("Failed to stop shade for %s", self.entity_id)

    def _handle_position_update(self, update: DeviceUpdate) -> None:
        """Handle a LIGHT_LEVEL (position) update."""
        if not update.value:
            _LOGGER.warning("Received empty position value for shade %s", self.entity_id)
            return

        try:
            # Value is a tuple of bytes, first element is the position (0-100)
            # Position is reported as a float
            position = float(update.value[0])

            # Clamp position to 0-100 range
            position = max(0.0, min(100.0, position))

            # Round to nearest integer for Home Assistant
            new_position = round(position)

            # Update previous position if current position is changing and distinct
            if (
                self._attr_current_cover_position is not None
                and new_position != self._attr_current_cover_position
            ):
                self._previous_position = self._attr_current_cover_position

            self._attr_current_cover_position = new_position

            self.async_write_ha_state()

            _LOGGER.debug(
                "Updated shade %s: position=%d (prev=%s), is_closed=%s, is_opening=%s, is_closing=%s",
                self.entity_id,
                self._attr_current_cover_position,
                self._previous_position,
                self.is_closed,
                self.is_opening,
                self.is_closing,
            )
        except (ValueError, IndexError):
            _LOGGER.exception(
                "Failed to parse shade position for %s: %s", self.entity_id, update.value
            )

    def _handle_motion_update(self, update: DeviceUpdate) -> None:
        """Handle a MOTOR_MYSTERY (motion state) update."""
        if not update.value:
            _LOGGER.warning("Received empty motion value for shade %s", self.entity_id)
            return

        try:
            # Value is a tuple of bytes, first element is the motor state
            # 0 = not moving, non-zero = moving
            motor_state = float(update.value[0])

            self._motor_mystery = motor_state

            self.async_write_ha_state()

            _LOGGER.debug(
                "Updated shade %s motion: motor_mystery=%f, is_opening=%s, is_closing=%s",
                self.entity_id,
                self._motor_mystery,
                self.is_opening,
                self.is_closing,
            )
        except (ValueError, IndexError):
            _LOGGER.exception(
                "Failed to parse shade motion for %s: %s", self.entity_id, update.value
            )
