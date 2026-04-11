"""Tests for Lutron QS connection handling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from lutron_integration import devices as lutron_devices, qse
from lutron_integration.recorded_session import SessionEvent
from lutron_integration.connection import DisconnectedError
from lutron_integration.types import DeviceAction, IntegrationIDMap, SerialNumber

from homeassistant.components.lutronqs import (
    LutronQSData,
    _log_traffic,
    _mark_all_entities_unavailable,
    connection_monitor,
    monitor_unsolicited_messages,
    process_device_updates,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry


def _make_device_details(sn: SerialNumber) -> qse.DeviceDetails:
    return qse.DeviceDetails(
        sn=sn,
        integration_id=b"Device",
        family=b"KEYPAD(1)",
        product=b"QSWS2-GB(1)",
        raw_attrs={},
    )


async def test_monitor_unsolicited_messages_signals_disconnect(
    hass: HomeAssistant,
) -> None:
    """Test unsolicited monitor signals disconnect to the reconnect loop."""
    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    conn = AsyncMock()
    conn.read_unsolicited.side_effect = DisconnectedError()
    config_entry.runtime_data = LutronQSData(
        connection=conn,
        universe=qse.LutronUniverse(),
    )

    await monitor_unsolicited_messages(hass, config_entry)

    assert config_entry.runtime_data.connection_lost_event.is_set()


def test_mark_all_entities_unavailable_clears_universe() -> None:
    """Test disconnect handling marks all entities unavailable immediately."""
    serial = SerialNumber(b"12345678")
    entity = MagicMock()
    runtime_data = LutronQSData(
        connection=AsyncMock(),
        universe=qse.LutronUniverse(
            devices_by_sn={serial: _make_device_details(serial)},
            iidmap=IntegrationIDMap(),
        ),
        entities_by_device={serial: [entity]},
    )

    config_entry = MockConfigEntry(domain="lutronqs", data={})
    config_entry.runtime_data = runtime_data

    _mark_all_entities_unavailable(config_entry)

    assert config_entry.runtime_data.universe.devices_by_sn == {}
    entity.async_write_ha_state.assert_called_once()


async def test_process_device_updates_restores_registered_devices(
    hass: HomeAssistant,
) -> None:
    """Test reconnect from an empty universe marks known devices available again."""
    serial = SerialNumber(b"12345678")
    conn = AsyncMock()
    entity = MagicMock()
    handler = MagicMock()
    update = lutron_devices.DeviceUpdate(
        serial_number=serial,
        component=1,
        action=DeviceAction.LIGHT_LEVEL,
        value=(b"100.00",),
    )

    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    config_entry.runtime_data = LutronQSData(
        connection=conn,
        universe=qse.LutronUniverse(),
        entity_routing_table={(serial, 1, DeviceAction.LIGHT_LEVEL): handler},
        entities_by_device={serial: [entity]},
        registered_devices={serial},
    )

    with (
        patch(
            "homeassistant.components.lutronqs.qse.enumerate_universe",
            AsyncMock(
                return_value=qse.LutronUniverse(
                    devices_by_sn={serial: _make_device_details(serial)},
                    iidmap=IntegrationIDMap(),
                )
            ),
        ),
        patch(
            "homeassistant.components.lutronqs.lutron_devices.probe_device",
            AsyncMock(return_value=[update]),
        ),
    ):
        await process_device_updates(hass, config_entry)

    handler.assert_called_once_with(update)
    entity.async_write_ha_state.assert_called_once()


async def test_connection_monitor_reconnects_and_restarts_unsolicited(
    hass: HomeAssistant,
) -> None:
    """Test reconnect path clears disconnect state and restarts monitor."""
    old_conn = AsyncMock()
    new_conn = AsyncMock()
    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    entity = MagicMock()
    serial = SerialNumber(b"12345678")
    config_entry.runtime_data = LutronQSData(
        connection=old_conn,
        universe=qse.LutronUniverse(
            devices_by_sn={serial: _make_device_details(serial)},
            iidmap=IntegrationIDMap(),
        ),
        entities_by_device={serial: [entity]},
    )
    config_entry.runtime_data.connection_lost_event.set()

    reconnected = asyncio.Event()

    async def fake_process_device_updates(
        _hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        if entry.runtime_data.connection is new_conn:
            reconnected.set()

    with (
        patch(
            "homeassistant.components.lutronqs._open_logged_connection",
            AsyncMock(return_value=(AsyncMock(), AsyncMock())),
        ),
        patch(
            "homeassistant.components.lutronqs.lutron_connection.login",
            AsyncMock(return_value=new_conn),
        ),
        patch(
            "homeassistant.components.lutronqs.process_device_updates",
            side_effect=fake_process_device_updates,
        ),
        patch("homeassistant.components.lutronqs._start_unsolicited_monitor") as restart,
        patch("homeassistant.components.lutronqs.asyncio.sleep", AsyncMock()),
    ):
        task = hass.async_create_task(connection_monitor(hass, config_entry))
        try:
            await reconnected.wait()
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass

    old_conn.disconnect.assert_awaited_once()
    restart.assert_called_once_with(hass, config_entry)
    entity.async_write_ha_state.assert_called_once()
    assert config_entry.runtime_data.connection is new_conn
    assert not config_entry.runtime_data.connection_lost_event.is_set()


def test_log_traffic_logs_direction_and_redaction(caplog) -> None:
    """Test raw traffic logging includes direction and redaction marker."""
    caplog.set_level("DEBUG")

    _log_traffic(
        "192.168.1.100",
        SessionEvent(direction="outgoing", contents=b"abc\r\n", is_redacted=True),
    )
    _log_traffic(
        "192.168.1.100",
        SessionEvent(direction="incoming", contents=b"~MONITORING,1,1\r\n"),
    )

    assert "Traffic 192.168.1.100 outgoing (redacted): b'abc\\r\\n'" in caplog.text
    assert "Traffic 192.168.1.100 incoming: b'~MONITORING,1,1\\r\\n'" in caplog.text
