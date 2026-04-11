"""The Lutron QS integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from lutron_integration import (
    connection as lutron_connection,
    devices as lutron_devices,
    qse,
)
from lutron_integration.types import SerialNumber, DeviceAction

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER, MODEL_HUB
from .entity import LutronQSEntity

HUB_FAMILY = b"CONTROL_INTERFACE(6)"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.COVER, Platform.EVENT, Platform.LIGHT, Platform.REMOTE]

@dataclass
class LutronQSData:
    """Lutron QS data stored in the config entry."""

    connection: lutron_connection.LutronConnection
    universe: qse.LutronUniverse
    # Maps (serial_number, component, action) to handler method for routing updates
    entity_routing_table: dict[
        tuple[SerialNumber, int, DeviceAction],
        Callable[[lutron_devices.DeviceUpdate], None],
    ] = field(default_factory=dict)
    # Maps device serial number to list of entities for that device
    entities_by_device: dict[SerialNumber, list[LutronQSEntity]] = field(default_factory=dict)
    # Factory functions to create entities for a device (per platform)
    # Each function takes (device_sn: SerialNumber, probe_results: list[DeviceUpdate] | None) and returns list of entities
    entity_factories: dict[
        Platform,
        Callable[[SerialNumber, list[lutron_devices.DeviceUpdate] | None], list[LutronQSEntity]],
    ] = field(default_factory=dict)
    # Hub device serial number (determined once during initial setup)
    hub_sn: SerialNumber | None = None
    # Set of device serial numbers that have been registered
    registered_devices: set[SerialNumber] = field(default_factory=set)
    # Signaled when the connection is known to be broken.
    connection_lost_event: asyncio.Event = field(default_factory=asyncio.Event)
    unsolicited_task: asyncio.Task[None] | None = None


type LutronQSConfigEntry = ConfigEntry[LutronQSData]

# TODO: This name is not descriptive.  It should be
# enumerate_and_update_status.
async def process_device_updates(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
) -> None:
    """Process device updates: enumerate universe and update device/entity states.

    This function:
    1. Enumerates the universe to get current device list
    2. On first run: determines which device is the hub
    3. For new devices: registers them in device registry and creates entities
    4. For existing devices: ensures they're marked available
    5. For missing devices: entities automatically become unavailable (via available property)
    """
    # Enumerate the universe
    new_universe = await qse.enumerate_universe(entry.runtime_data.connection)

    _LOGGER.debug(
        "Enumerated Lutron QS at %s, found %d devices",
        entry.data[CONF_HOST],
        len(new_universe.devices_by_sn),
    )

    # Track which devices appeared/disappeared for availability updates
    old_universe = entry.runtime_data.universe
    old_device_sns = set(old_universe.devices_by_sn.keys())
    new_device_sns = set(new_universe.devices_by_sn.keys())

    # On first run, old_device_sns will be empty (universe starts empty)
    # Only calculate differences on subsequent runs when old_device_sns is non-empty
    if old_device_sns:
        appeared_devices = new_device_sns - old_device_sns
        disappeared_devices = old_device_sns - new_device_sns
    else:
        appeared_devices = {
            device_sn
            for device_sn in new_device_sns
            if device_sn in entry.runtime_data.registered_devices
        }
        disappeared_devices = set()

    # Update the universe in runtime data
    entry.runtime_data.universe = new_universe

    # Determine hub device serial number if not already set
    if entry.runtime_data.hub_sn is None:
        hub_devices = [
            (sn, details)
            for sn, details in new_universe.devices_by_sn.items()
            if details.family == HUB_FAMILY
        ]

        if len(hub_devices) > 1:
            _LOGGER.warning(
                "Multiple hub devices found with family %s: %s. Using first one as parent.",
                HUB_FAMILY.decode(),
                [sn.sn.decode() for sn, _ in hub_devices],
            )

        # Use first hub if found, otherwise use first device as fallback
        if hub_devices:
            entry.runtime_data.hub_sn = hub_devices[0][0]
        else:
            _LOGGER.warning(
                "No hub device found with family %s, using first device as hub",
                HUB_FAMILY.decode(),
            )
            entry.runtime_data.hub_sn = next(iter(new_universe.devices_by_sn.keys()))

    device_registry = dr.async_get(hass)

    # Process each device in the current universe
    for device_sn, device_details in new_universe.devices_by_sn.items():
        serial = device_details.sn.sn.decode()

        # Check if this is a new device
        if device_sn not in entry.runtime_data.registered_devices:
            _LOGGER.info("Discovered new device %s", serial)

            # Resolve device to DeviceClass for logging
            device_class = lutron_devices.FAMILY_TO_CLASS.get(device_details.family)
            if device_class:
                _LOGGER.debug(
                    "Device %s (family: %s) resolved to DeviceClass with %d component groups",
                    serial,
                    device_details.family.decode(),
                    len(device_class.groups),
                )
            else:
                _LOGGER.info(
                    "Device %s has unknown family %s (%s)",
                    serial,
                    device_details.family.decode(),
                    device_details.product.decode(),
                )

            # Extract firmware and hardware versions from raw_attrs
            boot_version = device_details.raw_attrs.get(b"BOOT", b"").decode()
            code_version = device_details.raw_attrs.get(b"CODE", b"").decode()
            hw_version = device_details.raw_attrs.get(b"HW", b"").decode()

            # Format software version as "Boot: X Code: Y" if available
            sw_version = None
            if boot_version and code_version:
                sw_version = f"Boot: {boot_version} Code: {code_version}"
            elif code_version:
                sw_version = f"Code: {code_version}"
            elif boot_version:
                sw_version = f"Boot: {boot_version}"

            # Register device in Home Assistant
            device_entry = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, serial)},
                manufacturer=MANUFACTURER,
                model=f"{device_details.family.decode()} - {device_details.product.decode()}",
                name=device_details.integration_id.decode()
                if device_details.integration_id != b"(Not Set)"
                else serial,
                sw_version=sw_version,
                hw_version=hw_version if hw_version else None,
                via_device=(
                    (DOMAIN, entry.runtime_data.hub_sn.sn.decode())
                    if device_sn != entry.runtime_data.hub_sn
                    else None
                ),
            )
            _LOGGER.info(
                "Registered device %s (%s): %s (sw: %s, hw: %s)",
                serial,
                device_details.family.decode(),
                device_entry.id,
                sw_version or "unknown",
                hw_version or "unknown",
            )

            # Probe the device to learn its state and discover which components exist
            probe_results = await lutron_devices.probe_device(
                entry.runtime_data.connection,
                entry.runtime_data.universe.iidmap,
                device_sn
            )
            _LOGGER.debug(
                "Probed device %s, received %d component updates",
                serial,
                len(probe_results),
            )

            # Create entities for this device using the factory functions
            # Pass probe results so factories can determine which entities to create
            device_entities = []
            for platform, factory in entry.runtime_data.entity_factories.items():
                try:
                    entities = factory(device_sn, probe_results)
                    if entities:
                        device_entities.extend(entities)
                        _LOGGER.debug(
                            "Creating %d %s entities for device %s",
                            len(entities),
                            platform,
                            serial,
                        )
                except Exception:
                    _LOGGER.exception(
                        "Error creating %s entities for device %s", platform, serial
                    )

            # Store entity references for this device
            entry.runtime_data.entities_by_device[device_sn] = device_entities

            # Mark device as registered
            entry.runtime_data.registered_devices.add(device_sn)

    # Handle devices that appeared (came back online or reconnected after reset)
    for device_sn in appeared_devices:
        serial = device_sn.sn.decode()
        # Only log for devices that were previously registered (came back)
        # New devices were already logged above
        if device_sn in entry.runtime_data.registered_devices:
            _LOGGER.info("Device %s came back online", serial)

            # Probe device to learn its current state
            probe_results = await lutron_devices.probe_device(
                entry.runtime_data.connection,
                entry.runtime_data.universe.iidmap,
                device_sn
            )

            # Feed probe results through handlers to update entity state BEFORE marking available
            # This prevents stale values from briefly appearing when the device comes back online
            for update in probe_results:
                routing_key = (update.serial_number, update.component, update.action)
                handler = entry.runtime_data.entity_routing_table.get(routing_key)
                if handler:
                    handler(update)

            # Now trigger availability update for all entities of this device
            for entity in entry.runtime_data.entities_by_device.get(device_sn, []):
                entity.async_write_ha_state()

    # Handle devices that disappeared (went offline)
    for device_sn in disappeared_devices:
        serial = device_sn.sn.decode()
        _LOGGER.info("Device %s went offline", serial)

        # Trigger state update for all entities of this device to mark unavailable
        for entity in entry.runtime_data.entities_by_device.get(device_sn, []):
            entity.async_write_ha_state()


def _mark_all_entities_unavailable(entry: LutronQSConfigEntry) -> None:
    """Mark all known entities unavailable by clearing the current universe."""
    if not entry.runtime_data.universe.devices_by_sn:
        return

    entry.runtime_data.universe = qse.LutronUniverse()
    for entities in entry.runtime_data.entities_by_device.values():
        for entity in entities:
            entity.async_write_ha_state()


def _start_unsolicited_monitor(
    hass: HomeAssistant, entry: LutronQSConfigEntry
) -> None:
    """Ensure there is exactly one unsolicited monitor task for the current connection."""
    if (
        entry.runtime_data.unsolicited_task is not None
        and not entry.runtime_data.unsolicited_task.done()
    ):
        entry.runtime_data.unsolicited_task.cancel()

    entry.runtime_data.unsolicited_task = entry.async_create_background_task(
        hass,
        monitor_unsolicited_messages(hass, entry),
        name=f"lutronqs-{entry.entry_id}-monitor",
    )


async def connection_monitor(hass: HomeAssistant, entry: LutronQSConfigEntry) -> None:
    """Monitor connection health and re-enumerate devices periodically.

    This task:
    1. Re-enumerates devices every 30 seconds to detect changes
    2. Triggers dynamic device discovery when new devices appear
    3. Entities automatically become unavailable when devices disappear
    4. Reconnects with exponential backoff if connection fails
    """
    backoff_intervals = [1, 2, 4, 8, 10]  # seconds, caps at 10s
    backoff_index = 0

    _LOGGER.debug("Starting connection monitor")

    while True:
        reconnect_reason: str | None = None

        try:
            await asyncio.wait_for(entry.runtime_data.connection_lost_event.wait(), 30)
            reconnect_reason = "unsolicited monitor detected connection loss"
        except asyncio.TimeoutError:
            pass

        try:
            if reconnect_reason is None:
                # Re-enumerate the universe to detect device changes
                async with asyncio.timeout(10):
                    await process_device_updates(hass, entry)

                # Reset backoff on success
                backoff_index = 0
                continue

        except (
            asyncio.TimeoutError,
            OSError,
            lutron_connection.ProtocolError,
            lutron_connection.DisconnectedError,
        ) as err:
            reconnect_reason = str(err)

        try:
            _LOGGER.warning(
                "Connection lost to Lutron QS at %s: %s. Will attempt to reconnect.",
                entry.data[CONF_HOST],
                reconnect_reason,
            )

            entry.runtime_data.connection_lost_event.set()
            _mark_all_entities_unavailable(entry)

            # Close the failed connection immediately
            try:
                await entry.runtime_data.connection.disconnect()
            except Exception:
                _LOGGER.debug("Failed to disconnect old connection cleanly")

            # Connection failed - try to reconnect with backoff
            while True:
                await asyncio.sleep(backoff_intervals[backoff_index])
                backoff_index = min(backoff_index + 1, len(backoff_intervals) - 1)

                _LOGGER.info(
                    "Attempting to reconnect to Lutron QS at %s",
                    entry.data[CONF_HOST],
                )

                try:
                    # Try to reconnect
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(entry.data[CONF_HOST], 23), timeout=10.0
                    )

                    try:
                        conn = await asyncio.wait_for(
                            lutron_connection.login(
                                reader,
                                writer,
                                entry.data[CONF_USERNAME].encode("utf-8"),
                                (
                                    entry.data.get(CONF_PASSWORD, "").encode("utf-8")
                                    if entry.data.get(CONF_PASSWORD)
                                    else None
                                ),
                            ),
                            timeout=10.0,
                        )

                        # Update connection with the new one
                        entry.runtime_data.connection = conn
                        entry.runtime_data.connection_lost_event.clear()
                        _start_unsolicited_monitor(hass, entry)

                        # Re-enumerate universe and trigger discovery
                        await process_device_updates(hass, entry)

                        _LOGGER.info(
                            "Successfully reconnected to Lutron QS at %s",
                            entry.data[CONF_HOST],
                        )

                        # Reset backoff
                        backoff_index = 0

                        break  # Successfully reconnected

                    except lutron_connection.LoginError:
                        # Authentication failed - trigger reauth
                        _LOGGER.error(
                            "Authentication failed during reconnect to %s",
                            entry.data[CONF_HOST],
                        )
                        raise ConfigEntryAuthFailed(
                            f"Authentication failed for {entry.data[CONF_HOST]}"
                        )

                except (asyncio.TimeoutError, OSError) as reconnect_err:
                    _LOGGER.debug(
                        "Reconnection attempt failed: %s. Will retry with backoff.",
                        reconnect_err,
                    )
                    # Continue loop to retry with increased backoff

        except asyncio.CancelledError:
            _LOGGER.debug("Connection monitor cancelled")
            raise


async def monitor_unsolicited_messages(
    hass: HomeAssistant, entry: LutronQSConfigEntry
) -> None:
    """Monitor unsolicited messages from the Lutron connection and route to entities."""
    _LOGGER.debug("Starting unsolicited message monitoring")

    try:
        while True:
            # Read one unsolicited message
            message = await entry.runtime_data.connection.read_unsolicited()

            # Only process ~DEVICE messages
            if not message.startswith(b"~DEVICE,"):
                _LOGGER.debug("Ignoring non-device message: %s", message)
                continue

            # Parse the message (use current universe to get up-to-date iidmap)
            update = lutron_devices.decode_device_update(message, entry.runtime_data.universe.iidmap)
            if update is None:
                _LOGGER.debug(f"Unhandled unsolicited message {message!r}")
                continue

            _LOGGER.debug(f'Received update: {update!r}')

            # Look up the handler in the routing table
            routing_key = (update.serial_number, update.component, update.action)
            handler = entry.runtime_data.entity_routing_table.get(routing_key)

            if handler is None:
                _LOGGER.debug(
                    "No handler registered for device update: serial=%s, component=%d, action=%s",
                    update.serial_number,
                    update.component,
                    update.action,
                )
                continue

            # Call the handler method directly
            handler(update)

    except asyncio.CancelledError:
        _LOGGER.debug("Unsolicited message monitoring cancelled")
        raise
    except (
        OSError,
        lutron_connection.ProtocolError,
        lutron_connection.DisconnectedError,
    ) as err:
        entry.runtime_data.connection_lost_event.set()
        _LOGGER.debug("Unsolicited message monitor stopping after disconnect: %s", err)
    except Exception:
        _LOGGER.exception("Error in unsolicited message monitoring")


async def async_setup_entry(hass: HomeAssistant, entry: LutronQSConfigEntry) -> bool:
    """Set up Lutron QS from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    _LOGGER.debug(
        "Setting up Lutron QS integration for %s with username %s",
        host,
        username,
    )

    # Connect to the Lutron QS access point
    try:
        reader, writer = await asyncio.open_connection(host, 23)
    except (TimeoutError, OSError) as err:
        raise ConfigEntryNotReady(f"Unable to connect to {host}") from err

    _LOGGER.debug(f"TCP connection to {host} established")

    # Login with credentials
    try:
        conn = await lutron_connection.login(
            reader,
            writer,
            username.encode("utf-8"),
            password.encode("utf-8") if password else None,
        )
    except lutron_connection.LoginError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed for {host}") from err
    except (TimeoutError, OSError) as err:
        raise ConfigEntryNotReady(f"Connection failed during login to {host}") from err

    # Store runtime data (will be populated by platforms and enumeration)
    entry.runtime_data = LutronQSData(
        connection=conn,
        universe=qse.LutronUniverse(),  # Will be populated by process_device_updates
        hub_sn=None,  # Will be determined on first enumeration
    )

    # Forward setup to platforms FIRST - this registers the entity factory callbacks
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Now enumerate devices and create entities using the registered factories
    try:
        await process_device_updates(hass, entry)
    except (TimeoutError, OSError, lutron_connection.ProtocolError) as err:
        await conn.disconnect()
        raise ConfigEntryNotReady(f"Failed to enumerate devices on {host}") from err

    # Start monitoring unsolicited messages in a background task
    _start_unsolicited_monitor(hass, entry)

    # Start connection monitor for health checks and device discovery
    entry.async_create_background_task(
        hass,
        connection_monitor(hass, entry),
        name=f"lutronqs-{entry.entry_id}-connection-monitor",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LutronQSConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Close connection to device (if runtime_data was set)
        if hasattr(entry, "runtime_data") and entry.runtime_data:
            await entry.runtime_data.connection.disconnect()

    return unload_ok
