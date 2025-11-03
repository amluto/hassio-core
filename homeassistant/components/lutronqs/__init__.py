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
from pylutron_integration.types import SerialNumber

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
        tuple[SerialNumber, int, lutron_devices.Action],
        Callable[[lutron_devices.DeviceUpdate], None],
    ]


type LutronQSConfigEntry = ConfigEntry[LutronQSData]

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

    # Enumerate all devices in the Lutron system
    try:
        universe = await qse.enumerate_universe(conn)
    except (TimeoutError, OSError, lutron_connection.ProtocolError) as err:
        await conn.disconnect()
        raise ConfigEntryNotReady(f"Failed to enumerate devices on {host}") from err

    _LOGGER.debug(
        "Successfully connected to Lutron QS at %s, found %d devices",
        host,
        len(universe.devices_by_sn),
    )

    # Store runtime data with empty routing table (will be populated by platforms)
    entry.runtime_data = LutronQSData(
        connection=conn, universe=universe, entity_routing_table={}
    )

    # Manually register devices in the device registry
    device_registry = dr.async_get(hass)

    # Identify hub device(s) by family
    hub_devices = [
        (sn, details)
        for sn, details in universe.devices_by_sn.items()
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
        hub_sn, hub_details = next(iter(universe.devices_by_sn.items()))

    _LOGGER.info('Starting to register devices and such')

    # Register all discovered devices and resolve to DeviceClass
    for device_sn, device_details in universe.devices_by_sn.items():
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

        # TODO: Consider showing a friendlier name based on the DeviceClass?

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

        await entry.runtime_data.connection.probe_device(device_details.sn)

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start monitoring unsolicited messages in a background task
    entry.async_create_background_task(
        hass,
        monitor_unsolicited_messages(hass, entry),
        name=f"lutronqs-{entry.entry_id}-monitor",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LutronQSConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Close connection to device
        await entry.runtime_data.connection.disconnect()

    return unload_ok
