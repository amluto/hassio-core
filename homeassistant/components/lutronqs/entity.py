"""Base entity for Lutron QS integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
import logging
from typing import TYPE_CHECKING

from pylutron_integration.devices import DeviceUpdate
from pylutron_integration.types import SerialNumber, DeviceAction

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from . import LutronQSConfigEntry

_LOGGER = logging.getLogger(__name__)


class LutronQSEntity(Entity, ABC):
    """Base entity for Lutron QS devices."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize the Lutron QS entity.

        Args:
            entry: Config entry
            device_sn: Device serial number
            component_number: Component number
            unique_id_suffix: Optional suffix for unique ID (e.g., "scene")
        """
        self._entry = entry
        self._device_sn = device_sn
        self._component_number = component_number

        serial = device_sn.sn.decode()

        # Set unique ID based on serial number and component number
        # Format: [serial]-[component] or [serial]-[component]-[suffix]
        if unique_id_suffix:
            self._attr_unique_id = f"{serial}-{component_number}-{unique_id_suffix}"
        else:
            self._attr_unique_id = f"{serial}-{component_number}"

        # Set device info to link entity to device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Entity is unavailable if its device is no longer present in the universe.
        This handles cases where devices are powered down or removed from the network.
        """
        return self._device_sn in self._entry.runtime_data.universe.devices_by_sn

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Register action handlers in the routing table
        for action, handler in self.get_routing_actions():
            routing_key = (self._device_sn, self._component_number, action)
            self._entry.runtime_data.entity_routing_table[routing_key] = handler

        _LOGGER.debug(
            "Registered %s for routing: serial=%s, component=%d, actions=%s",
            self.entity_id,
            self._device_sn,
            self._component_number,
            [action.name for action, _ in self.get_routing_actions()],
        )

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is being removed from hass."""
        # Unregister from routing table
        for action, _ in self.get_routing_actions():
            routing_key = (self._device_sn, self._component_number, action)
            self._entry.runtime_data.entity_routing_table.pop(routing_key, None)

    @abstractmethod
    def get_routing_actions(
        self,
    ) -> list[tuple[DeviceAction, Callable[[DeviceUpdate], None]]]:
        """Return the list of (action, handler) tuples for this entity."""
