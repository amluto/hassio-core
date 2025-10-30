"""Support for Lutron QS scene controllers as remotes.

Note: This platform is specifically for the GrafikEyeQS SCENE_CONTROLLER component.
The scene controller allows selecting one of 17 scenes (0-16), where scene 0 is "off"
and scenes 1-16 are defined externally to this integration.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import TYPE_CHECKING, Any

from pylutron_integration.devices import Action, SerialNumber

from homeassistant.components.remote import ATTR_ACTIVITY, RemoteEntity, RemoteEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronQSConfigEntry
from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from . import DeviceUpdate

_LOGGER = logging.getLogger(__name__)

# Scene 0 = off, scenes 1-16 are defined externally
# These are always the same - there is no scene enumeration
MIN_SCENE = 0
MAX_SCENE = 16

# Generate activity list: ["Scene 1", "Scene 2", ..., "Scene 16"]
ACTIVITY_LIST = [f"Scene {i}" for i in range(1, MAX_SCENE + 1)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Lutron QS scene controllers from a config entry."""
    universe = entry.runtime_data.universe
    entities: list[LutronQSSceneController] = []

    # Iterate over all devices and create scene controller remotes
    for device_sn, device_details in universe.devices_by_sn.items():
        # Only handle GrafikEyeQS for now
        if device_details.family == b"GRAFIK_EYE(2)":
            # Get the SCENE_CONTROLLER component group
            from pylutron_integration.devices import FAMILY_TO_CLASS

            device_class = FAMILY_TO_CLASS.get(device_details.family)
            if not device_class:
                continue

            scene_controller_group = device_class.groups.get("SCENE_CONTROLLER")
            if not scene_controller_group:
                continue

            # Scene controller has exactly one component (component number 141)
            component_number = scene_controller_group.component_number(1)
            if component_number is None:
                continue

            entity = LutronQSSceneController(
                entry,
                device_sn,
                component_number,
                device_details.integration_id.decode()
                if device_details.integration_id != b"(Not Set)"
                else str(device_sn),
            )
            entities.append(entity)

    async_add_entities(entities, True)


class LutronQSSceneController(RemoteEntity):
    """Representation of a Lutron QS scene controller as a remote."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = RemoteEntityFeature.ACTIVITY

    def __init__(
        self,
        entry: LutronQSConfigEntry,
        device_sn: SerialNumber,
        component_number: int,
        device_name: str,
    ) -> None:
        """Initialize a Lutron QS scene controller."""
        self._entry = entry
        self._device_sn = device_sn
        self._component_number = component_number
        self._device_name = device_name

        serial = device_sn.sn.decode()

        # Unique ID: serial_number_component_number
        self._attr_unique_id = f"{serial}_{component_number}"

        # Entity name: "Scene controller"
        self._attr_name = "Scene controller"

        # Device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
        )

        # State: scene 0 = off, scenes 1-16 = on
        self._current_scene: int = 0

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        # Register this entity in the routing table for CURRENT_SCENE updates
        routing_key = (
            self._device_sn,
            self._component_number,
            Action.CURRENT_SCENE.value,
        )
        self._entry.runtime_data.entity_routing_table[routing_key] = self

        _LOGGER.debug(
            "Registered scene controller %s for routing: serial=%s, component=%d, action=CURRENT_SCENE",
            self.entity_id,
            self._device_sn,
            self._component_number,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is being removed from hass."""
        # Unregister from routing table
        routing_key = (
            self._device_sn,
            self._component_number,
            Action.CURRENT_SCENE.value,
        )
        self._entry.runtime_data.entity_routing_table.pop(routing_key, None)

    @property
    def is_on(self) -> bool:
        """Return True if the scene controller is on (scene != 0)."""
        return self._current_scene != 0

    @property
    def current_activity(self) -> str | None:
        """Return the current active scene."""
        if self._current_scene == 0:
            return None
        return f"Scene {self._current_scene}"

    @property
    def activity_list(self) -> list[str]:
        """Return the list of available scenes."""
        return ACTIVITY_LIST

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the scene controller and optionally select an activity (scene).

        If activity is provided, activate that scene. Otherwise, this is a no-op.
        """
        if activity := kwargs.get(ATTR_ACTIVITY):
            # Parse "Scene N" to get scene number
            try:
                # Activity format is "Scene N" where N is 1-16
                if activity.startswith("Scene "):
                    scene = int(activity[6:])  # Extract number after "Scene "
                    if 1 <= scene <= MAX_SCENE:
                        await self._send_scene(scene)
                    else:
                        _LOGGER.warning(
                            "Invalid scene number %d in activity '%s' for %s",
                            scene,
                            activity,
                            self.entity_id,
                        )
                else:
                    _LOGGER.warning(
                        "Invalid activity format '%s' for %s (expected 'Scene N')",
                        activity,
                        self.entity_id,
                    )
            except (ValueError, IndexError):
                _LOGGER.warning(
                    "Failed to parse activity '%s' for %s",
                    activity,
                    self.entity_id,
                )
        else:
            _LOGGER.debug(
                "Turn on called without activity for %s - no action taken",
                self.entity_id,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the scene controller (set to scene 0)."""
        await self._send_scene(0)

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send a command to the scene controller.

        Not used for scene controllers - use turn_on with activity instead.
        """
        _LOGGER.warning(
            "send_command called for scene controller %s - use turn_on with activity parameter instead",
            self.entity_id,
        )

    async def _send_scene(self, scene: int) -> None:
        """Send a scene selection command to the device.

        Args:
            scene: Scene number (0-16)
        """
        # Send CURRENT_SCENE command to device
        # Format: #DEVICE,serial,component,7,scene
        command = (
            f"#DEVICE,{self._device_sn.sn.decode()},"
            f"{self._component_number},{Action.CURRENT_SCENE.value},{scene}"
        ).encode("utf-8")

        _LOGGER.debug("Setting scene to %d: %s", scene, command)

        try:
            await self._entry.runtime_data.connection.raw_query(command)
        except Exception:
            _LOGGER.exception("Failed to set scene for %s", self.entity_id)

    def handle_update(self, update: DeviceUpdate) -> None:
        """Handle a device update for this scene controller.

        Called when a ~DEVICE message is received with matching serial/component/action.
        """
        # We only care about CURRENT_SCENE actions for scene controllers
        if update.action != Action.CURRENT_SCENE.value:
            _LOGGER.debug(
                "Scene controller %s ignoring non-CURRENT_SCENE action: %d",
                self.entity_id,
                update.action,
            )
            return

        if not update.value:
            _LOGGER.warning("Received empty value for scene controller %s", self.entity_id)
            return

        try:
            # Value is a tuple of bytes, first element is the scene number (0-16)
            scene = int(update.value[0])

            if MIN_SCENE <= scene <= MAX_SCENE:
                self._current_scene = scene
                self.async_write_ha_state()

                _LOGGER.debug(
                    "Updated scene controller %s: scene=%d, activity=%s",
                    self.entity_id,
                    scene,
                    self.current_activity,
                )
            else:
                _LOGGER.warning(
                    "Received invalid scene number %d for %s (expected %d-%d)",
                    scene,
                    self.entity_id,
                    MIN_SCENE,
                    MAX_SCENE,
                )
        except (ValueError, IndexError):
            _LOGGER.exception(
                "Failed to parse scene number for %s: %s", self.entity_id, update.value
            )
