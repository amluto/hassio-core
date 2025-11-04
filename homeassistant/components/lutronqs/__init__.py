"""The Lutron QS integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging

from pylutron_integration import (
    connection as lutron_connection,
    devices as lutron_devices,
    qse,
)
from pylutron_integration.types import SerialNumber, DeviceAction

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER, MODEL_HUB

HUB_FAMILY = b"CONTROL_INTERFACE(6)"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.COVER, Platform.LIGHT, Platform.REMOTE]

@dataclass
class LutronQSData:
    """Lutron QS data stored in the config entry."""

    connection: lutron_connection.LutronConnection
    universe: qse.LutronUniverse
    # Maps (serial_number, component, action) to handler method for routing updates
    entity_routing_table: dict[
        tuple[SerialNumber, int, DeviceAction],
        Callable[[lutron_devices.DeviceUpdate], None],
    ]
    # Callbacks for adding entities dynamically (per platform)
    add_entities_callbacks: dict[Platform, Callable] = None  # type: ignore[assignment]
    # Discovery functions to call when new devices appear (per platform)
    discover_new_devices: dict[Platform, Callable] = None  # type: ignore[assignment]


type LutronQSConfigEntry = ConfigEntry[LutronQSData]


async def enumerate_and_discover_devices(
    hass: HomeAssistant,
    entry: LutronQSConfigEntry,
    initial_setup: bool = False,
) -> None:
    """Enumerate devices from the Lutron system and handle discovery.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        initial_setup: If True, register devices in device registry and probe them.
                      If False, only update universe and trigger entity discovery.
    """
    # Enumerate the universe
    new_universe = await qse.enumerate_universe(entry.runtime_data.connection)

    _LOGGER.debug(
        "Enumerated Lutron QS at %s, found %d devices",
        entry.data[CONF_HOST],
        len(new_universe.devices_by_sn),
    )

    # Update the universe in runtime data
    entry.runtime_data.universe = new_universe

    if initial_setup:
        # Initial setup: register all devices in device registry
        device_registry = dr.async_get(hass)

        # Identify hub device(s) by family
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
            hub_sn, hub_details = hub_devices[0]
        else:
            _LOGGER.warning(
                "No hub device found with family %s, using first device as hub",
                HUB_FAMILY.decode(),
            )
            hub_sn, hub_details = next(iter(new_universe.devices_by_sn.items()))

        _LOGGER.info("Starting to register devices")

        # Register all discovered devices
        for device_sn, device_details in new_universe.devices_by_sn.items():
            serial = device_details.sn.sn.decode()

            # Resolve device to DeviceClass
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
                via_device=(DOMAIN, hub_sn.sn.decode()) if device_sn != hub_sn else None,
            )
            _LOGGER.info(
                "Registered device %s (%s): %s (sw: %s, hw: %s)",
                serial,
                device_details.family.decode(),
                device_entry.id,
                sw_version or "unknown",
                hw_version or "unknown",
            )

            # Probe each device
            await entry.runtime_data.connection.probe_device(device_details.sn)

    else:
        # Not initial setup: trigger entity discovery on all platforms
        for platform, discover_func in entry.runtime_data.discover_new_devices.items():
            try:
                await discover_func()
            except Exception:
                _LOGGER.exception(
                    "Error during device discovery for platform %s", platform
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
        await asyncio.sleep(30)  # Check every 30 seconds

        try:
            # Re-enumerate the universe to detect device changes
            async with asyncio.timeout(10):
                await enumerate_and_discover_devices(hass, entry, initial_setup=False)

            # Reset backoff on success
            backoff_index = 0

        except (asyncio.TimeoutError, OSError, lutron_connection.ProtocolError) as err:
            _LOGGER.warning(
                "Connection lost to Lutron QS at %s: %s. Will attempt to reconnect.",
                entry.data[CONF_HOST],
                err,
            )

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

                        # Re-enumerate universe and trigger discovery
                        await enumerate_and_discover_devices(hass, entry, initial_setup=False)

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
    conn = entry.runtime_data.connection
    universe = entry.runtime_data.universe

    try:
        while True:
            # Read one unsolicited message
            message = await conn.read_unsolicited()
            _LOGGER.debug(f"Received unsolicited message {message!r}")

            # Only process ~DEVICE messages
            if not message.startswith(b"~DEVICE,"):
                _LOGGER.debug("Ignoring non-device message: %s", message)
                continue

            # Parse the message
            update = lutron_devices.decode_device_update(message, universe)
            if update is None:
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

    # Store runtime data with empty routing table (will be populated by platforms)
    # Note: universe will be populated by enumerate_and_discover_devices below
    entry.runtime_data = LutronQSData(
        connection=conn,
        universe=None,  # Will be set immediately below
        entity_routing_table={},
        add_entities_callbacks={},
        discover_new_devices={},
    )

    # Enumerate all devices and register them in the device registry
    try:
        await enumerate_and_discover_devices(hass, entry, initial_setup=True)
    except (TimeoutError, OSError, lutron_connection.ProtocolError) as err:
        await conn.disconnect()
        raise ConfigEntryNotReady(f"Failed to enumerate devices on {host}") from err

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start monitoring unsolicited messages in a background task
    entry.async_create_background_task(
        hass,
        monitor_unsolicited_messages(hass, entry),
        name=f"lutronqs-{entry.entry_id}-monitor",
    )

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
